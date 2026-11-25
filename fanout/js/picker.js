// Visual picker. KIND ("input" | "send" | "message") is defined before this script runs.
// The result is left in window.__fanoutPicked for Python to poll.
if (window.__fanoutPickStop) window.__fanoutPickStop();
window.__fanoutPicked = null;
window.__fanoutPickActive = true;

const css = (el, text) => { el.style.cssText = text; return el; };
const make = (id, text) => { const el = document.createElement("div"); el.id = id; return css(el, text); };
const box = make("__fanout_pick_box__",
  "position:fixed;pointer-events:none;border:2px solid #e03131;background:rgba(224,49,49,.12);z-index:2147483646;display:none");
const tip = make("__fanout_pick_tip__",
  "position:fixed;pointer-events:none;background:#212529;color:#fff;font:12px monospace;padding:3px 6px;" +
  "border-radius:4px;max-width:480px;word-break:break-all;z-index:2147483647;display:none");
const bar = make("__fanout_pick_bar__",
  "position:fixed;top:28px;left:0;right:0;height:26px;background:#e03131;color:#fff;font:600 13px system-ui,sans-serif;" +
  "display:flex;align-items:center;justify-content:center;pointer-events:none;z-index:2147483647");
bar.textContent = "PICK MODE - click the element, ESC to cancel";
document.documentElement.append(box, tip, bar);

function elementFor(el) {
  if (KIND === "input") return el.closest("textarea, input, [contenteditable='true']") || el;
  if (KIND === "send") return el.closest("button, [role='button'], a, input[type='submit']") || el;
  return el;
}

function selectorFor(el) {
  if (KIND === "message") {
    for (const s of FanOutSel.MESSAGE_SELECTORS) {
      if (el.closest(s)) return s;
    }
  }
  return FanOutSel.build(elementFor(el));
}

let target = null;
function targetAt(x, y) {
  const el = document.elementFromPoint(x, y);
  const ok = el && el.nodeType === 1 && !FanOutSel.isOwn(el) && el !== document.documentElement && el !== document.body;
  return ok ? el : null;
}

function onMove(e) {
  const el = targetAt(e.clientX, e.clientY);
  target = el;
  if (!el) { box.style.display = "none"; tip.style.display = "none"; return; }
  const r = elementFor(el).getBoundingClientRect();
  css(box, "position:fixed;pointer-events:none;border:2px solid #e03131;background:rgba(224,49,49,.12);z-index:2147483646;" +
    "left:" + r.left + "px;top:" + r.top + "px;width:" + r.width + "px;height:" + r.height + "px");
  tip.textContent = selectorFor(el);
  const below = r.bottom + 4 + 28 < innerHeight;
  css(tip, "position:fixed;pointer-events:none;background:#212529;color:#fff;font:12px monospace;padding:3px 6px;" +
    "border-radius:4px;max-width:480px;word-break:break-all;z-index:2147483647;" +
    "left:" + Math.max(0, Math.min(r.left, innerWidth - 300)) + "px;top:" + (below ? r.bottom + 4 : Math.max(54, r.top - 28)) + "px");
}

function swallow(e) {
  if (e.target && FanOutSel.isOwn(e.target)) return;
  e.preventDefault();
  e.stopPropagation();
  e.stopImmediatePropagation();
}

function onClick(e) {
  swallow(e);
  const el = targetAt(e.clientX, e.clientY) || target;
  if (el) finish({ ok: true, selector: selectorFor(el), kind: KIND });
}

function onKey(e) {
  if (e.key === "Escape") { swallow(e); finish({ ok: false, cancelled: true, kind: KIND }); }
}

const BLOCKED = ["mousedown", "mouseup", "pointerdown", "pointerup", "auxclick", "dblclick", "contextmenu"];

function stop() {
  window.removeEventListener("mousemove", onMove, true);
  window.removeEventListener("click", onClick, true);
  window.removeEventListener("keydown", onKey, true);
  BLOCKED.forEach((t) => window.removeEventListener(t, swallow, true));
  [box, tip, bar].forEach((el) => el.remove());
  window.__fanoutPickActive = false;
}

function finish(result) { stop(); window.__fanoutPicked = result; }

window.__fanoutPickStop = stop;
window.addEventListener("mousemove", onMove, true);
window.addEventListener("click", onClick, true);
window.addEventListener("keydown", onKey, true);
BLOCKED.forEach((t) => window.addEventListener(t, swallow, true));
return true;
