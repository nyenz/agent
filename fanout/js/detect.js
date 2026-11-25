// Auto-detect the three things FanOut needs on a chat page. Returns selectors, or null for each one not found.
const usable = (el) => !el.disabled && !el.readOnly && FanOutSel.visible(el);

function findInput() {
  const areas = Array.from(document.querySelectorAll("textarea")).filter(usable);
  if (areas.length) return areas[0];
  const editable = Array.from(document.querySelectorAll('[contenteditable="true"]')).filter(
    (el) => FanOutSel.visible(el) && !(el.parentElement && el.parentElement.closest('[contenteditable="true"]')));
  if (editable.length) return editable[0];
  const inputs = Array.from(document.querySelectorAll('input:not([type]), input[type="text"], input[type="search"]')).filter(usable);
  return inputs.length ? inputs[inputs.length - 1] : null;
}

function findSend(inputEl) {
  const re = /send|submit/i;
  const buttons = Array.from(document.querySelectorAll("button, [role='button'], input[type='submit']")).filter((b) => {
    if (!FanOutSel.visible(b)) return false;
    const label = [b.getAttribute("aria-label"), b.getAttribute("data-testid"), b.getAttribute("title")].join(" ");
    return re.test(label) || /^send$/i.test((b.innerText || b.value || "").trim());
  });
  if (!buttons.length) return null;
  if (inputEl) {
    const r = inputEl.getBoundingClientRect();
    const dist = (b) => {
      const q = b.getBoundingClientRect();
      return Math.hypot(q.left + q.width / 2 - (r.left + r.width / 2), q.top + q.height / 2 - (r.top + r.height / 2));
    };
    buttons.sort((a, b) => dist(a) - dist(b));
  }
  return buttons[0];
}

function findMessageSelector() {
  for (const s of FanOutSel.MESSAGE_SELECTORS) {
    if (FanOutSel.matches(s).some((el) => !FanOutSel.isOwn(el))) return s;
  }
  return null;
}

const inputEl = findInput();
const sendEl = findSend(inputEl);
return {
  input: inputEl ? FanOutSel.build(inputEl) : null,
  send: sendEl ? FanOutSel.build(sendEl) : null,
  message: findMessageSelector(),
};
