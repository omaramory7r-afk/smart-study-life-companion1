import json
import os
import sqlite3
import uuid
import base64
import hashlib
import hmac
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from scheduler import ScheduleRequest, generate_schedule
try:
    from openai import OpenAI
except ImportError:  # Keep non-AI features usable before optional dependencies are installed.
    OpenAI = None

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(Path(__file__).resolve().parent / '.env')

app = FastAPI(title='Smart Study & Life Companion API', version='0.4.0')
client = OpenAI(api_key=os.getenv('OPENAI_API_KEY')) if OpenAI and os.getenv('OPENAI_API_KEY') else None
MODEL = os.getenv('OPENAI_MODEL', 'gpt-4.1-mini')
MAX_FILE_BYTES = 15 * 1024 * 1024
MAX_TOTAL_BYTES = 60 * 1024 * 1024
MAX_FILES = 15
DB_PATH = Path(os.getenv('SSC_DATABASE_PATH', Path(__file__).resolve().parent / 'data' / 'schedule.db'))
AUTH_SECRET = os.getenv('SSC_AUTH_SECRET', 'development-only-change-this-secret-before-deploying')
bearer = HTTPBearer(auto_error=False)


def init_schedule_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            name TEXT NOT NULL, password_hash TEXT NOT NULL, created_at TEXT NOT NULL
        )''')
        conn.execute('''CREATE TABLE IF NOT EXISTS schedule_plans (
            id TEXT PRIMARY KEY, created_at TEXT NOT NULL, request_json TEXT NOT NULL, result_json TEXT NOT NULL,
            user_id TEXT REFERENCES users(id)
        )''')
        columns = {row[1] for row in conn.execute('PRAGMA table_info(schedule_plans)')}
        if 'user_id' not in columns:
            conn.execute('ALTER TABLE schedule_plans ADD COLUMN user_id TEXT REFERENCES users(id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_schedule_user_created ON schedule_plans(user_id, created_at DESC)')
        conn.commit()


def save_schedule_plan(request: ScheduleRequest, result: dict[str, Any], user_id: str) -> dict[str, Any]:
    plan_id = str(uuid.uuid4())
    created_at = datetime.now(timezone.utc).isoformat()
    result = {**result, 'plan_id': plan_id, 'saved_at': created_at}
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            'INSERT INTO schedule_plans (id, created_at, request_json, result_json, user_id) VALUES (?, ?, ?, ?, ?)',
            (plan_id, created_at, json.dumps(request.model_dump(mode='json'), ensure_ascii=False), json.dumps(result, ensure_ascii=False), user_id),
        )
        conn.commit()
    return result


def read_schedule_plan(row):
    if not row:
        return None
    return {
        'plan_id': row[0],
        'created_at': row[1],
        'request': json.loads(row[2]),
        'result': json.loads(row[3]),
    }


class AuthInput(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)
    name: str | None = Field(default=None, min_length=2, max_length=80)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip('=')


def _password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac('sha256', password.encode(), salt, 310000)
    return _b64(salt) + '.' + _b64(digest)


def _password_ok(password: str, encoded: str) -> bool:
    try:
        salt_s, digest_s = encoded.split('.', 1)
        salt = base64.urlsafe_b64decode(salt_s + '=' * (-len(salt_s) % 4))
        return hmac.compare_digest(_password_hash(password, salt), encoded)
    except Exception:
        return False


def _make_token(user_id: str) -> str:
    payload = _b64(json.dumps({'sub': user_id, 'exp': int((datetime.now(timezone.utc) + timedelta(days=7)).timestamp())}).encode())
    sig = _b64(hmac.new(AUTH_SECRET.encode(), payload.encode(), hashlib.sha256).digest())
    return payload + '.' + sig


def current_user(credentials: HTTPAuthorizationCredentials | None = Depends(bearer)):
    if not credentials:
        raise HTTPException(401, 'سجّل الدخول أولاً للمتابعة.')
    try:
        payload, signature = credentials.credentials.split('.', 1)
        expected = _b64(hmac.new(AUTH_SECRET.encode(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            raise ValueError('signature')
        raw = base64.urlsafe_b64decode(payload + '=' * (-len(payload) % 4))
        data = json.loads(raw)
        if int(data['exp']) < int(datetime.now(timezone.utc).timestamp()):
            raise ValueError('expired')
        with sqlite3.connect(DB_PATH) as conn:
            row = conn.execute('SELECT id, email, name FROM users WHERE id=?', (data['sub'],)).fetchone()
        if not row:
            raise ValueError('user')
        return {'id': row[0], 'email': row[1], 'name': row[2]}
    except Exception:
        raise HTTPException(401, 'جلسة الدخول غير صالحة أو انتهت. سجّل الدخول مرة أخرى.')


init_schedule_db()


class AIRequest(BaseModel):
    text: str = Field(min_length=1, max_length=100000)
    extra: dict[str, Any] = Field(default_factory=dict)


class QuizGradeRequest(BaseModel):
    questions: list[dict[str, Any]] = Field(min_length=1, max_length=45)
    answers: list[dict[str, Any]] = Field(min_length=1, max_length=45)


@app.post('/api/auth/register')
def register(data: AuthInput):
    email = data.email.strip().lower()
    if '@' not in email or '.' not in email.rsplit('@', 1)[-1]:
        raise HTTPException(422, 'اكتب بريداً إلكترونياً صحيحاً.')
    user_id = str(uuid.uuid4())
    name = (data.name or email.split('@')[0]).strip()
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute('INSERT INTO users (id,email,name,password_hash,created_at) VALUES (?,?,?,?,?)',
                         (user_id, email, name, _password_hash(data.password), datetime.now(timezone.utc).isoformat()))
            conn.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(409, 'هذا البريد مسجل بالفعل. سجّل الدخول بدلاً من ذلك.')
    return {'access_token': _make_token(user_id), 'token_type': 'bearer', 'user': {'id': user_id, 'email': email, 'name': name}}


@app.post('/api/auth/login')
def login(data: AuthInput):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute('SELECT id,email,name,password_hash FROM users WHERE email=? COLLATE NOCASE', (data.email.strip(),)).fetchone()
    if not row or not _password_ok(data.password, row[3]):
        raise HTTPException(401, 'البريد الإلكتروني أو كلمة المرور غير صحيحة.')
    return {'access_token': _make_token(row[0]), 'token_type': 'bearer', 'user': {'id': row[0], 'email': row[1], 'name': row[2]}}


@app.get('/api/auth/me')
def me(user=Depends(current_user)):
    return {'user': user}


@app.post('/api/schedule/generate')
def generate_schedule_endpoint(request: ScheduleRequest, user=Depends(current_user)):
    """Generate and persist a schedule owned by the authenticated student."""
    try:
        result = generate_schedule(request)
    except ValueError as exc:
        raise HTTPException(422, str(exc))
    return save_schedule_plan(request, result, user['id'])


@app.get('/api/schedule/plans/latest')
def latest_schedule_plan(user=Depends(current_user)):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            'SELECT id, created_at, request_json, result_json FROM schedule_plans WHERE user_id=? ORDER BY created_at DESC LIMIT 1',
            (user['id'],)
        ).fetchone()
    plan = read_schedule_plan(row)
    if not plan:
        raise HTTPException(404, 'لا يوجد جدول محفوظ لهذا الحساب حتى الآن.')
    return plan


@app.get('/api/schedule/plans/{plan_id}')
def get_schedule_plan(plan_id: str, user=Depends(current_user)):
    with sqlite3.connect(DB_PATH) as conn:
        row = conn.execute(
            'SELECT id, created_at, request_json, result_json FROM schedule_plans WHERE id = ? AND user_id=?',
            (plan_id, user['id']),
        ).fetchone()
    plan = read_schedule_plan(row)
    if not plan:
        raise HTTPException(404, 'الجدول المطلوب غير موجود.')
    return plan


def require_client():
    if not client:
        raise HTTPException(503, 'خدمة الذكاء الاصطناعي غير مفعلة. أضف OPENAI_API_KEY إلى backend/.env ثم أعد تشغيل الخادم.')


def ask(instruction: str, text: str, limit: int = 4000, json_mode: bool = False):
    require_client()
    try:
        kwargs = {
            'model': MODEL,
            'messages': [
                {'role': 'system', 'content': instruction},
                {'role': 'user', 'content': text},
            ],
            'max_tokens': limit,
        }
        if json_mode:
            kwargs['response_format'] = {'type': 'json_object'}
        result = client.chat.completions.create(**kwargs)
        return {'text': result.choices[0].message.content or ''}
    except HTTPException:
        raise
    except Exception as exc:
        # Avoid returning provider internals or secrets to the browser.
        raise HTTPException(502, f'تعذر الاتصال بخدمة الذكاء الاصطناعي: {type(exc).__name__}')


@app.get('/api/health')
def health():
    return {'status': 'ok', 'ai_configured': bool(client), 'model': MODEL, 'version': app.version}


@app.post('/api/files/extract')
async def extract_files(files: list[UploadFile] = File(...)):
    """Extract text from multiple documents, preserving the multipart upload order."""
    if not files:
        raise HTTPException(400, 'اختر ملفاً واحداً على الأقل.')
    if len(files) > MAX_FILES:
        raise HTTPException(413, f'الحد الأقصى {MAX_FILES} ملفاً في الطلب الواحد.')

    docs = []
    text_parts = []
    total = 0
    for position, upload in enumerate(files, start=1):
        name = Path(upload.filename or f'file-{position}').name
        suffix = Path(name).suffix.lower()
        raw = await upload.read(MAX_FILE_BYTES + 1)
        total += len(raw)
        if len(raw) > MAX_FILE_BYTES:
            raise HTTPException(413, f'الملف {name} أكبر من 15 ميجابايت.')
        if total > MAX_TOTAL_BYTES:
            raise HTTPException(413, 'إجمالي الملفات تجاوز 60 ميجابايت.')

        try:
            if suffix == '.pdf':
                from pypdf import PdfReader
                import io
                reader = PdfReader(io.BytesIO(raw))
                parts = [(page.extract_text() or '').strip() for page in reader.pages]
                extracted = '\n\n'.join(part for part in parts if part)
                if not extracted.strip():
                    raise HTTPException(422, f'لم أجد نصاً قابلاً للاستخراج في {name}. إذا كان PDF ممسوحاً ضوئياً فسيحتاج OCR في مرحلة لاحقة.')
            elif suffix == '.docx':
                from docx import Document
                import io
                document = Document(io.BytesIO(raw))
                extracted = '\n'.join(p.text for p in document.paragraphs if p.text.strip())
                for table in document.tables:
                    for row in table.rows:
                        extracted += '\n' + ' | '.join(cell.text.strip() for cell in row.cells)
            elif suffix in {'.txt', '.md', '.csv'}:
                extracted = raw.decode('utf-8-sig')
            else:
                raise HTTPException(415, f'صيغة {suffix or "غير معروفة"} غير مدعومة حالياً. استخدم PDF أو DOCX أو TXT أو MD أو CSV.')
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(422, f'تعذر قراءة الملف {name}: {type(exc).__name__}')

        if not extracted.strip():
            raise HTTPException(422, f'الملف {name} لا يحتوي على نص قابل للاستخدام.')
        docs.append({'order': position, 'name': name, 'characters': len(extracted)})
        text_parts.append(f"===== الملف {position}: {name} =====\n{extracted}")

    combined = '\n\n'.join(text_parts)
    truncated = len(combined) > 100000
    if truncated:
        combined = combined[:100000]
    return {'documents': docs, 'text': combined, 'file_count': len(docs), 'characters': len(combined), 'truncated': truncated}


@app.post('/api/explain')
def explain(x: AIRequest):
    langs = x.extra.get('languages', ['Arabic'])
    return ask(
        'أنت مدرس جامعي دقيق. اشرح المحتوى بوضوح وباللغات المطلوبة، وحافظ على المصطلحات العلمية. نظّم الشرح بعناوين وأمثلة وأسئلة مراجعة.',
        f"اللغات: {langs}\nالمستوى: {x.extra.get('level', 'مبسط')}\nأسلوب الشرح: {x.extra.get('style', 'خطوة بخطوة')}\nالمحتوى:\n{x.text}",
        6000,
    )


@app.post('/api/translate')
def translate(x: AIRequest):
    return ask(
        'أنت مترجم أكاديمي. حافظ على ترتيب الفقرات والعناوين والجداول قدر الإمكان، ولا تحذف أي معلومة. ضع المصطلح الأصلي بين قوسين عند طلب ذلك.',
        f"الترجمة إلى: {x.extra.get('target', 'العربية')}\nاحتفظ بالمصطلحات العلمية: {x.extra.get('keep_terms', True)}\nالنص:\n{x.text}",
        6000,
    )


@app.post('/api/summarize')
def summarize(x: AIRequest):
    return ask(
        'أنت خبير تلخيص تعليمي. أنشئ ملخصاً منظماً، أهم التعريفات، النقاط الأساسية، وأسئلة مراجعة. أضف خريطة ذهنية نصية متدرجة باستخدام التفرعات.',
        f"لغة الناتج: {x.extra.get('language', 'العربية')}\nنوع الملخص: {x.extra.get('style', 'مركز للامتحان')}\nالمحتوى:\n{x.text}",
        5500,
    )


@app.post('/api/chat')
def chat(x: AIRequest):
    return ask('أنت مساعد عام ذكي وودود. أجب بلغة المستخدم، واطلب التوضيح عند الحاجة، وميّز بين الحقائق والاحتمالات.', x.text)


@app.post('/api/quiz')
def quiz(x: AIRequest):
    count = min(45, max(1, int(x.extra.get('count', 10))))
    types = x.extra.get('types', ['multiple_choice', 'true_false'])
    prompt = f'''أنشئ {count} سؤالاً من المادة فقط. الأنواع المطلوبة: {types}. مستوى الصعوبة: {x.extra.get('difficulty', 'متنوع')}.
أخرج JSON صالحاً بالشكل التالي:
{{"questions":[{{"type":"multiple_choice","question":"...","options":["...","...","...","..."],"correct_index":0,"correct_answer":"...","explanation":"...","topic":"..."}},{{"type":"true_false","question":"...","options":["صح","خطأ"],"correct_index":0,"correct_answer":"صح","explanation":"...","topic":"..."}},{{"type":"essay","question":"...","options":[],"correct_answer":"إجابة نموذجية مختصرة","explanation":"معيار الإجابة","rubric":["عنصر 1","عنصر 2"],"topic":"..."}}]}}
التزم بالأنواع المطلوبة ووزعها قدر الإمكان. لا تضف أسئلة من خارج المحتوى. لا تضع أي نص خارج JSON.
المادة:
{x.text}'''
    raw = ask('أنت واضع اختبارات جامعية. أخرج JSON فقط دون Markdown.', prompt, 6500, json_mode=True)['text']
    try:
        data = json.loads(raw)
        questions = data.get('questions', [])[:count]
        if not questions:
            raise ValueError('empty')
        normalized = []
        for q in questions:
            qtype = q.get('type', 'multiple_choice')
            if qtype not in {'multiple_choice', 'true_false', 'essay'}:
                qtype = 'multiple_choice'
            options = q.get('options') or (['صح', 'خطأ'] if qtype == 'true_false' else [])
            normalized.append({
                'type': qtype,
                'question': str(q.get('question', '')),
                'options': options,
                'correct_index': q.get('correct_index'),
                'correct_answer': str(q.get('correct_answer', '')),
                'explanation': str(q.get('explanation', '')),
                'rubric': q.get('rubric', []),
                'topic': str(q.get('topic', 'عام')),
            })
        return {'questions': normalized}
    except Exception:
        raise HTTPException(502, 'لم أتمكن من قراءة صيغة الاختبار. حاول مرة أخرى أو قلل عدد الأسئلة.')


@app.post('/api/quiz/grade')
def grade_quiz(x: QuizGradeRequest):
    answers_by_index = {int(a.get('index', -1)): a.get('answer', '') for a in x.answers}
    results = []
    objective_total = 0
    objective_correct = 0
    essay_items = []

    for i, q in enumerate(x.questions):
        answer = answers_by_index.get(i, '')
        qtype = q.get('type', 'multiple_choice')
        if qtype == 'essay':
            essay_items.append({'index': i, 'question': q.get('question', ''), 'model_answer': q.get('correct_answer', ''), 'rubric': q.get('rubric', []), 'answer': str(answer)})
            results.append({'index': i, 'type': 'essay', 'answer': answer, 'correct': None, 'score': None, 'feedback': ''})
        else:
            objective_total += 1
            try:
                correct = int(answer) == int(q.get('correct_index'))
            except (ValueError, TypeError):
                correct = False
            objective_correct += int(correct)
            results.append({'index': i, 'type': qtype, 'answer': answer, 'correct': correct, 'score': 1 if correct else 0, 'correct_answer': q.get('correct_answer') or (q.get('options') or [''])[int(q.get('correct_index') or 0)], 'explanation': q.get('explanation', ''), 'topic': q.get('topic', 'عام')})

    essay_summary = ''
    if essay_items:
        require_client()
        prompt = 'قيّم إجابات الطالب المقالية من 0 إلى 1 لكل سؤال وفق الإجابة النموذجية وعناصر التقييم. كن منصفاً ولا تشترط التطابق الحرفي. أخرج JSON فقط بالشكل {"grades":[{"index":0,"score":0.7,"feedback":"..."}]}\n' + json.dumps(essay_items, ensure_ascii=False)
        raw = ask('أنت مصحح أكاديمي منصف. أخرج JSON فقط.', prompt, 2500, json_mode=True)['text']
        try:
            grades = json.loads(raw).get('grades', [])
            for g in grades:
                idx = int(g['index'])
                for item in results:
                    if item['index'] == idx and item['type'] == 'essay':
                        item['score'] = max(0.0, min(1.0, float(g.get('score', 0))))
                        item['feedback'] = str(g.get('feedback', ''))
        except Exception:
            raise HTTPException(502, 'تعذر تصحيح الإجابات المقالية. أعد المحاولة.')

    earned = sum(float(r.get('score') or 0) for r in results)
    total = len(results)
    percentage = round((earned / total) * 100) if total else 0
    topic_stats = {}
    for r in results:
        q = x.questions[r['index']]
        topic = q.get('topic', 'عام')
        stat = topic_stats.setdefault(topic, {'earned': 0.0, 'total': 0})
        stat['earned'] += float(r.get('score') or 0)
        stat['total'] += 1
    strengths, weaknesses = [], []
    for topic, stat in topic_stats.items():
        pct = stat['earned'] / stat['total'] * 100 if stat['total'] else 0
        (strengths if pct >= 70 else weaknesses).append({'topic': topic, 'percentage': round(pct)})
    return {'results': results, 'score': round(earned, 2), 'total': total, 'percentage': percentage, 'strengths': strengths, 'weaknesses': weaknesses}


@app.post('/api/assignment')
def assignment(x: AIRequest):
    return ask('ساعد الطالب على فهم التكليف. اشرح خطوات الحل، واذكر الافتراضات، ولا تختلق بيانات غير موجودة.', f"طريقة المساعدة: {x.extra.get('mode', 'شرح الخطوات')}\nالتكليف:\n{x.text}", 5000)


@app.post('/api/project')
def project(x: AIRequest):
    return ask('أنت مستشار مشروعات. قدم هدفاً ونطاقاً وخطوات وجدولاً زمنياً ومخرجات ومخاطر ومواد مطلوبة.', x.text, 5000)


@app.post('/api/presentation')
def presentation(x: AIRequest):
    return ask('أنت مصمم عروض تعليمية. اكتب مخطط شرائح واضحاً بعناوين ونقاط وملاحظات متحدث، مع اقتراحات بصرية.', x.text, 5500)


@app.get('/')
def home():
    return FileResponse(ROOT / 'frontend' / 'index.html')


app.mount('/static', StaticFiles(directory=ROOT / 'frontend'), name='static')
