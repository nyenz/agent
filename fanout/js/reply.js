// Read the newest reply on the page. MESSAGE_SELECTOR is defined before this runs
// (empty text means "no reply area known yet": read the whole page instead).
const els = MESSAGE_SELECTOR ? FanOutSel.matches(MESSAGE_SELECTOR).filter((e) => !FanOutSel.isOwn(e)) : [];
const last = els.length ? els[els.length - 1] : null;
let html = "";
let text = "";
if (last) {
  text = last.innerText || "";
  const copy = last.cloneNode(true);
  copy.querySelectorAll("button, [role='button'], svg, script, style, [aria-hidden='true']").forEach((n) => n.remove());
  html = copy.innerHTML;
} else {
  // innerText of a hidden element is its raw source, so never read script/style/template parts.
  const SKIP = /^(SCRIPT|STYLE|NOSCRIPT|TEMPLATE|LINK|META)$/;
  text = Array.from(document.body.children).filter((c) => !FanOutSel.isOwn(c) && !SKIP.test(c.tagName)).map((c) => c.innerText || "").join("\n");
}
return { text, html, count: els.length, busy: !!FanOutSel.findStop(), blocked: FanOutSel.blocked() };
