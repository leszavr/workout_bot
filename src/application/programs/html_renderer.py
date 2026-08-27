"""WorkoutProgramHtmlRenderer: HTML-программа для пользователя (Stage 5).

Renderer отделён от Telegram-хендлеров и от способа хранения media:
он принимает WorkoutProgram + список ``ExerciseMediaItem`` (готовые изображения:
data-URI или абсолютные URL) и генерирует автономный HTML.

Режимы медиа задаются содержимым ExerciseMediaItem.src:
- data-URI (embedded) — файл работает офлайн сразу после получения;
- абсолютный URL media endpoint — компактный файл, требует сеть.

Renderer НЕ предполагает фиксированное число изображений: 0/1/N — всё
отрисовывается одинаково надёжно. Placeholder'ы запрещены: нет изображения —
блок изображений не выводится.
"""
from __future__ import annotations

import html as html_module
from dataclasses import dataclass, field

from src.domain.program import ProgramExercise, TrainingDay, WorkoutProgram
from src.errors import HtmlRenderError

DAY_COLORS = [
    ("#f78166", "rgba(247,129,102,.08)"),
    ("#58a6ff", "rgba(88,166,255,.08)"),
    ("#3fb950", "rgba(63,185,80,.08)"),
    ("#e3b341", "rgba(227,179,65,.08)"),
    ("#d2a8ff", "rgba(210,168,255,.08)"),
    ("#79c0ff", "rgba(121,192,255,.08)"),
    ("#ff7b72", "rgba(255,123,114,.08)"),
]


@dataclass
class ExerciseMediaItem:
    """Изображение упражнения, подготовленное для рендеринга.

    src — data-URI (``data:image/webp;base64,...``) либо абсолютный https URL.
    """

    exercise_external_id: str
    sequence: int
    src: str


def _esc(value: str | None) -> str:
    return html_module.escape(value) if value else ""


def _technique_items(text: str | None) -> list[str]:
    if not text:
        return []
    items: list[str] = []
    for line in text.splitlines():
        line = line.strip().lstrip("0123456789.-) ")
        if line:
            items.append(line)
    return items


def _reps_badge(exercise: ProgramExercise) -> str:
    if exercise.repetitions_max > exercise.repetitions_min:
        return f"{exercise.sets} &times; {exercise.repetitions_min}&ndash;{exercise.repetitions_max}"
    return f"{exercise.sets} &times; {exercise.repetitions_min}"


def _exercise_card(
    order: int,
    exercise: ProgramExercise,
    exercise_by_id: dict[str, "ExerciseInfo"],
    media: list[ExerciseMediaItem],
) -> str:
    info = exercise_by_id.get(exercise.exercise_external_id)
    name_ru = info.name_ru if info and info.name_ru else None
    name_en = info.name if info else None

    badges = [f'<span class="badge reps">&#x1F501; {_reps_badge(exercise)}</span>']
    badges.append(
        f'<span class="badge" title="Отдых между подходами">'
        f"&#x23F1; отдых {exercise.rest_seconds} сек</span>"
    )
    if exercise.intensity:
        badges.append(f'<span class="badge weight">&#x2696; {_esc(exercise.intensity)}</span>')

    images_html = ""
    imgs = [m for m in media if m.src]
    if imgs:
        images_html = (
            '<div class="ex-images">'
            + "".join(
                f'<img src="{_esc(m.src)}" alt="{_esc(name_ru or name_en or exercise.exercise_external_id)}" loading="lazy"/>'
                for m in imgs
            )
            + "</div>"
        )

    technique = _technique_items(exercise.technique_notes or (info.technique if info else None))
    technique_html = ""
    if technique:
        technique_html = (
            '<div class="technique"><h5>Техника</h5><ol>'
            + "".join(f"<li>{_esc(item)}</li>" for item in technique)
            + "</ol></div>"
        )

    tip_html = ""
    if exercise.notes:
        tip_html = f'<div class="ex-tip">{_esc(exercise.notes)}</div>'

    warnings = info.warnings if info else []
    warning_html = ""
    if warnings:
        warning_html = (
            '<div class="ex-tip warning">&#x26A0; '
            + "; ".join(_esc(w) for w in warnings)
            + "</div>"
        )

    return f"""<div class="exercise-card">
  <div class="exercise-header" onclick="toggleEx(this)">
    <div class="ex-num">{order}</div>
    <div class="ex-info">
      <div class="ex-name">{_esc(name_ru or (name_en or exercise.exercise_external_id))}</div>
      <div class="ex-name-en">{_esc(name_en) if name_ru else ""}</div>
      <div class="ex-sets">{"".join(badges)}</div>
    </div>
    <span class="ex-arrow">&#x25BE;</span>
  </div>
  <div class="exercise-detail">
    {images_html}
    {technique_html}
    {tip_html}
    {warning_html}
  </div>
</div>"""


def _day_section(
    day: TrainingDay,
    color: str,
    exercise_by_id: dict[str, "ExerciseInfo"],
    media_by_exercise: dict[str, list[ExerciseMediaItem]],
) -> str:
    cards = []
    for order, exercise in enumerate(day.exercises, start=1):
        cards.append(
            _exercise_card(
                order,
                exercise,
                exercise_by_id,
                media_by_exercise.get(exercise.exercise_external_id, []),
            )
        )
    return f"""<div class="day-section" id="day-{day.day_number}" data-title="День {day.day_number} — {_esc(day.title)}">
<div class="block-card note"><p>&#x1F3AF; Фокус: <strong>{_esc(day.focus)}</strong></p></div>
{"".join(cards)}
</div>"""


@dataclass
class ExerciseInfo:
    """Данные каталога, необходимые рендереру (имена, техника, предупреждения)."""

    external_id: str
    name: str
    name_ru: str | None = None
    technique: str | None = None
    warnings: list[str] = field(default_factory=list)


def render_program_html(
    program: WorkoutProgram,
    *,
    exercise_info: list[ExerciseInfo] | None = None,
    media: list[ExerciseMediaItem] | None = None,
) -> str:
    """Генерирует автономный мобильный HTML программы тренировок."""
    if not program.training_days:
        raise HtmlRenderError("Программа не содержит тренировочных дней")

    info_by_id = {e.external_id: e for e in (exercise_info or [])}
    media_by_exercise: dict[str, list[ExerciseMediaItem]] = {}
    for item in media or []:
        media_by_exercise.setdefault(item.exercise_external_id, []).append(item)

    day_labels = " / ".join(f"День {d.day_number}" for d in program.training_days)

    duration_line = (
        f"{program.duration_weeks} нед &middot; {program.training_days_per_week} "
        f"тр/нед &middot; версия {program.version}"
    )

    safety_html = ""
    if program.safety_notes:
        safety_html = (
            """<div class="block-card safety">
  <h4>&#x26A0; Безопасность</h4>
  <ul>"""
            + "".join(f"<li>{_esc(note)}</li>" for note in program.safety_notes)
            + "</ul></div>"
        )

    progression_html = ""
    if program.progression.description or program.progression.weekly_increase_percent:
        increase = ""
        if program.progression.weekly_increase_percent is not None:
            pct = program.progression.weekly_increase_percent
            increase = f"Плановая прибавка: до {pct:g}% нагрузки в неделю.<br/>"
        progression_html = (
            """<div class="progress-box">
  <h4>&#x1F4C8; Прогрессия нагрузки</h4>
  <p>"""
            + increase
            + _esc(program.progression.description or "")
            + "</p></div>"
        )

    nav_parts = []
    sections = []
    for index, day in enumerate(program.training_days):
        color, bg = DAY_COLORS[index % len(DAY_COLORS)]
        nav_parts.append(
            f"""<div class="day-tab" id="tab-{day.day_number}" onclick="showDay({day.day_number})"
 style="--day-color:{color};--day-bg:{bg}">
  <div class="dt-label">День {day.day_number}</div>
  <div class="dt-name">{_esc(day.title)}</div>
  <div class="dt-badge">{len(day.exercises)} упр.</div>
</div>"""
        )
        sections.append(_day_section(day, color, info_by_id, media_by_exercise))

    return f"""<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1.0"/>
<title>{_esc(program.title)}</title>
<style>
{CSS}
</style>
</head>
<body>
<div class="header">
  <div class="header-badge">Программа &middot; {day_labels}</div>
  <h1>&#x1F4AA; {_esc(program.title)}</h1>
  <p>{duration_line}</p>
</div>

<div class="timer-anchor" id="timerAnchor"></div>
<div class="timer-spacer" id="timerSpacer"></div>
<div class="timer-wrap" id="timerWrap">
  <div class="timer-lbl">&#x23F1; Таймер отдыха</div>
  <div class="timer-disp" id="timerDisp">1:30</div>
  <div class="timer-btns">
    <button class="btn-t" onclick="setTimer(60,this)">60 сек</button>
    <button class="btn-t act" onclick="setTimer(90,this)">1:30</button>
    <button class="btn-t" onclick="setTimer(120,this)">2:00</button>
    <button class="btn-t" onclick="setTimer(180,this)">3:00</button>
    <button class="btn-go" onclick="startTimer()">&#x25BA; Старт</button>
    <button class="btn-rst" onclick="resetTimer()">&#x21BA; Сброс</button>
  </div>
</div>

<div class="principles">
  <div class="principles-hdr open" onclick="togglePrinciples(this)">
    <span>&#x1F4CB; О программе</span>
    <span class="arr">&#x25BE;</span>
  </div>
  <div class="principles-body open" id="principles-body">
    {('<p>' + _esc(program.description) + '</p>') if program.description else ''}
    {safety_html}
    {progression_html}
  </div>
</div>

<div class="days-nav">
{"".join(nav_parts)}
</div>

{"".join(sections)}

<script>
{JS}
</script>
</body>
</html>
"""


CSS = """:root{
  --bg:#0d1117;--surface:#161b22;--surface2:#1c2333;--surface3:#21262d;
  --border:#30363d;--border2:#484f58;
  --accent:#58a6ff;--green:#3fb950;--red:#f78166;--gold:#e3b341;--purple:#d2a8ff;
  --text:#e6edf3;--text2:#8b949e;--text3:#6e7681;
  --r:10px;--r-lg:16px;
  --ff:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
}
*,*::before,*::after{box-sizing:border-box;margin:0;padding:0}
body{font-family:var(--ff);background:var(--bg);color:var(--text);font-size:16px;
  line-height:1.6;padding-bottom:80px}
.header{background:linear-gradient(135deg,#0f1f3d 0%,#1a0f3d 100%);
  padding:24px 18px 20px;border-bottom:1px solid var(--border)}
.header-badge{display:inline-block;background:rgba(88,166,255,.18);
  border:1px solid var(--accent);border-radius:20px;
  padding:3px 12px;font-size:11px;color:var(--accent);margin-bottom:8px;font-weight:600}
.header h1{font-size:20px;font-weight:700;color:#fff;margin-bottom:4px}
.header p{font-size:12px;color:var(--text2)}
.timer-wrap{background:var(--surface2);border:1px solid var(--border);
  border-radius:var(--r-lg);padding:12px 14px;margin:14px 14px 0;text-align:center}
/* Приклеенный таймер: пока идёт отдых, отсчёт остаётся виден при прокрутке
   к следующему упражнению. Полупрозрачный и сжатый, чтобы не закрывать текст. */
.timer-anchor{height:0}
.timer-spacer{display:none}
.timer-spacer.on{display:block}
.timer-wrap.pinned{position:fixed;top:0;left:0;right:0;z-index:900;
  margin:0;padding:7px 12px;border-top:none;border-left:none;border-right:none;
  border-radius:0 0 var(--r-lg) var(--r-lg);
  display:flex;align-items:center;gap:10px;text-align:left;
  background:rgba(28,35,51,.88);
  -webkit-backdrop-filter:blur(10px);backdrop-filter:blur(10px);
  box-shadow:0 2px 12px rgba(0,0,0,.5);opacity:.9;transition:opacity .2s}
.timer-wrap.pinned:hover,.timer-wrap.pinned:active{opacity:1}
.timer-wrap.pinned .timer-lbl{display:none}
.timer-wrap.pinned .timer-disp{font-size:24px;margin:0;flex-shrink:0;letter-spacing:0}
.timer-wrap.pinned .timer-btns{margin-left:auto;gap:5px;flex-wrap:nowrap}
.timer-wrap.pinned .btn-t{display:none}
.timer-wrap.pinned .btn-go,.timer-wrap.pinned .btn-rst{padding:6px 12px;font-size:12px}
.timer-lbl{font-size:11px;text-transform:uppercase;letter-spacing:.9px;
  color:var(--text2);margin-bottom:6px;font-weight:600}
.timer-disp{font-size:38px;font-weight:700;color:var(--accent);
  font-variant-numeric:tabular-nums;margin-bottom:8px;letter-spacing:-1px;
  transition:color .3s}
.timer-btns{display:flex;gap:6px;justify-content:center;flex-wrap:wrap}
.btn-t{background:var(--surface);border:1px solid var(--border);color:var(--text);
  border-radius:8px;padding:8px 12px;font-size:13px;cursor:pointer;
  font-family:var(--ff);-webkit-tap-highlight-color:transparent;transition:all .15s}
.btn-t.act{background:var(--accent);border-color:var(--accent);color:#0d1117;font-weight:700}
.btn-go{background:var(--green);border:none;color:#0d1117;
  border-radius:8px;padding:8px 18px;font-size:13px;font-weight:700;
  cursor:pointer;font-family:var(--ff);-webkit-tap-highlight-color:transparent}
.btn-rst{background:var(--surface2);border:1px solid var(--border);color:var(--text2);
  border-radius:8px;padding:8px 18px;font-size:13px;font-weight:700;
  cursor:pointer;font-family:var(--ff);-webkit-tap-highlight-color:transparent}
.principles{margin:14px 14px 0;background:var(--surface);
  border:1px solid var(--border);border-radius:var(--r-lg);overflow:hidden}
.principles-hdr{display:flex;align-items:center;justify-content:space-between;
  padding:12px 14px;cursor:pointer;-webkit-tap-highlight-color:transparent}
.principles-hdr span{font-size:13px;font-weight:700;color:var(--accent)}
.principles-hdr .arr{color:var(--accent);transition:transform .3s;font-size:16px}
.principles-hdr.open .arr{transform:rotate(180deg)}
.principles-body{display:none;padding:0 14px 14px}
.principles-body.open{display:block}
.principles-body p{font-size:13px;color:var(--text)}
.block-card{border-radius:var(--r-lg);padding:12px 14px;margin-bottom:10px;border:1px solid var(--border)}
.block-card h4{font-size:11px;font-weight:700;text-transform:uppercase;
  letter-spacing:.8px;color:var(--text2);margin-bottom:6px}
.block-card p{font-size:14px;color:var(--text);margin-bottom:3px}
.block-card strong{color:var(--green)}
.block-card.note{background:var(--surface);font-size:13px;color:var(--text2)}
.block-card.note p{font-size:13px;color:var(--text2)}
.block-card.note strong{color:var(--text)}
.block-card.safety{background:linear-gradient(135deg,#1a0d0d,#140a0a);border-color:#5c2b25}
.block-card.safety h4{color:var(--red)}
.block-card.safety ul{padding-left:18px;font-size:13px;color:var(--text)}
.block-card.safety li{margin-bottom:4px}
.progress-box{background:var(--surface2);border-radius:8px;padding:10px 12px;
  margin-top:8px;border-left:3px solid var(--gold)}
.progress-box h4{font-size:11px;font-weight:700;color:var(--gold);
  text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.progress-box p{font-size:13px;color:var(--text)}
.days-nav{display:flex;gap:8px;margin:14px 14px 0;overflow-x:auto;padding-bottom:2px}
.days-nav::-webkit-scrollbar{display:none}
.day-tab{flex-shrink:0;flex:1;padding:10px 6px;border-radius:var(--r-lg);
  border:2px solid var(--border);background:var(--surface);
  cursor:pointer;-webkit-tap-highlight-color:transparent;
  font-family:var(--ff);text-align:center;transition:all .15s}
.day-tab .dt-label{font-size:11px;color:var(--text3);text-transform:uppercase;
  letter-spacing:.6px;margin-bottom:3px}
.day-tab .dt-name{font-size:13px;font-weight:700;color:var(--text2)}
.day-tab .dt-badge{font-size:10px;margin-top:3px;
  padding:2px 7px;border-radius:10px;display:inline-block;font-weight:600;
  background:var(--surface2);color:var(--text2)}
.day-tab.active{border-color:var(--day-color);background:var(--day-bg)}
.day-tab.active .dt-name{color:var(--day-color)}
.day-tab.active .dt-badge{background:var(--day-bg);color:var(--day-color)}
.day-section{display:none;padding:12px 14px 0}
.day-section.active{display:block}
.exercise-card{background:var(--surface);border:1px solid var(--border);
  border-radius:var(--r-lg);margin-bottom:10px;overflow:hidden}
.exercise-header{display:flex;align-items:center;padding:12px 14px;
  cursor:pointer;-webkit-tap-highlight-color:transparent;gap:10px}
.exercise-header.open .ex-arrow{transform:rotate(180deg)}
.ex-num{width:28px;height:28px;border-radius:50%;background:var(--surface2);
  display:flex;align-items:center;justify-content:center;
  font-size:12px;font-weight:700;color:var(--accent);flex-shrink:0}
.ex-info{flex:1;min-width:0}
.ex-name{font-size:15px;font-weight:700;color:var(--text)}
.ex-name-en{font-size:11px;color:var(--text3);margin-top:1px}
.ex-sets{display:flex;gap:5px;margin-top:4px;flex-wrap:wrap}
.badge{background:var(--surface2);border:1px solid var(--border);border-radius:20px;
  padding:2px 8px;font-size:11px;color:var(--text2)}
.badge.weight{color:var(--gold);border-color:#3a2a00;background:#1e1500}
.badge.reps{color:var(--green);border-color:#0a2a15;background:#0a1f10}
.ex-arrow{font-size:18px;color:var(--text3);transition:transform .25s;flex-shrink:0}
.exercise-detail{display:none;padding:0 14px 14px;border-top:1px solid var(--border)}
.exercise-detail.open{display:block}
.ex-images{display:flex;gap:8px;margin:10px 0;overflow-x:auto;
  -webkit-overflow-scrolling:touch;padding-bottom:4px}
.ex-images img{height:130px;width:auto;max-width:90vw;border-radius:8px;object-fit:cover;
  flex-shrink:0;border:1px solid var(--border)}
.technique{background:var(--surface2);border-radius:8px;padding:10px 12px;margin-bottom:8px}
.technique h5{font-size:11px;font-weight:700;text-transform:uppercase;
  letter-spacing:.8px;color:var(--accent);margin-bottom:6px}
.technique ol{padding-left:16px;font-size:13px;color:var(--text)}
.technique ol li{margin-bottom:4px}
.ex-tip{background:#1a1500;border:1px solid var(--gold);border-radius:8px;
  padding:8px 12px;font-size:12px;color:var(--gold);margin-top:6px}
.ex-tip.warning{background:#1c0e0e;border-color:var(--red);color:var(--red)}
@media print{
  .days-nav,.timer-wrap,.timer-spacer{display:none}
  body{background:#fff;color:#000;padding:0;font-size:11pt}
  .day-section{display:block!important;padding:0}
  .day-section::before{content:attr(data-title);
    display:block;font-size:16pt;font-weight:700;
    margin:16pt 0 8pt;border-bottom:2pt solid #333;padding-bottom:4pt}
  .exercise-card,.block-card,.progress-box{border:1px solid #ccc;border-radius:4px;
    margin-bottom:8pt;break-inside:avoid;background:#fff}
  .exercise-detail{display:block!important}
  @page{margin:12mm;size:A4}
}"""

JS = """function showDay(n) {
  document.querySelectorAll('.day-section').forEach(function(s){s.classList.remove('active')});
  document.querySelectorAll('.day-tab').forEach(function(t){t.classList.remove('active')});
  var sec = document.getElementById('day-' + n);
  var tab = document.getElementById('tab-' + n);
  if (sec) sec.classList.add('active');
  if (tab) tab.classList.add('active');
}
function togglePrinciples(hdr) {
  var body = hdr.nextElementSibling;
  var open = body.classList.contains('open');
  body.classList.toggle('open', !open);
  hdr.classList.toggle('open', !open);
}
function toggleEx(hdr) {
  var det = hdr.nextElementSibling;
  var open = det.classList.contains('open');
  det.classList.toggle('open', !open);
  hdr.classList.toggle('open', !open);
}
var tSec = 90, tSet = 90, tRunning = false, tInt = null, tAutoReset = null;
function _cancelAutoReset() {
  if (tAutoReset) { clearTimeout(tAutoReset); tAutoReset = null; }
}
/* Приклеивание таймера к верху экрана.

   Работает только при запущенном отсчёте: в покое таймер остаётся на своём
   месте в потоке страницы. Spacer компенсирует высоту, которую элемент теряет
   при переходе в position:fixed, иначе содержимое подпрыгивало бы. */
function _pinTimer(on) {
  var wrap = document.getElementById('timerWrap');
  var spacer = document.getElementById('timerSpacer');
  if (!wrap || !spacer) return;
  if (on === wrap.classList.contains('pinned')) return;
  if (on) {
    spacer.style.height = wrap.offsetHeight + 'px';
    spacer.classList.add('on');
    wrap.classList.add('pinned');
  } else {
    wrap.classList.remove('pinned');
    spacer.classList.remove('on');
    spacer.style.height = '';
  }
}
function _syncPin() {
  var anchor = document.getElementById('timerAnchor');
  if (!anchor) return;
  _pinTimer(tRunning && anchor.getBoundingClientRect().top < 0);
}
window.addEventListener('scroll', _syncPin, { passive: true });
window.addEventListener('resize', _syncPin);
function setTimer(s, btn) {
  tSet = s; tSec = s;
  if (tRunning) { clearInterval(tInt); tRunning = false; }
  _cancelAutoReset();
  document.querySelector('.btn-go').textContent = '\\u25BA Старт';
  updTimer();
  document.querySelectorAll('.btn-t').forEach(function(b){ b.classList.remove('act'); });
  if (btn) btn.classList.add('act');
  _syncPin();
}
function updTimer() {
  var m = Math.floor(tSec / 60), s = tSec % 60;
  var el = document.getElementById('timerDisp');
  el.textContent = m + ':' + (s < 10 ? '0' : '') + s;
  el.style.color = tSec === 0 ? 'var(--green)' : tSec <= 10 ? 'var(--red)' : 'var(--accent)';
}
function startTimer() {
  var btn = document.querySelector('.btn-go');
  if (tRunning) {
    clearInterval(tInt); tRunning = false;
    btn.textContent = '\\u25BA Старт';
    _syncPin();
    return;
  }
  _cancelAutoReset();
  if (tSec === 0) { tSec = tSet; }
  tRunning = true;
  btn.textContent = '\\u23F8 Пауза';
  _syncPin();
  tInt = setInterval(function() {
    if (tSec > 0) { tSec--; updTimer(); }
    if (tSec === 0) {
      clearInterval(tInt); tRunning = false;
      btn.textContent = '\\u25BA Старт';
      if ('vibrate' in navigator) navigator.vibrate([300, 100, 300]);
      // Ноль виден секунду, затем таймер сам встаёт на исходное время:
      // «Сброс» нужен только чтобы прервать неоконченный отдых.
      tAutoReset = setTimeout(function() {
        tAutoReset = null; tSec = tSet; updTimer(); _syncPin();
      }, 1000);
    }
  }, 1000);
}
function resetTimer() {
  clearInterval(tInt); tRunning = false;
  _cancelAutoReset(); tSec = tSet;
  document.querySelector('.btn-go').textContent = '\\u25BA Старт';
  updTimer();
  _syncPin();
}
showDay(1);"""
