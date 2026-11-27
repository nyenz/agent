const FanOutSel = (() => {
  const OWN = "__fanout";
  const ATTRS = ["data-testid", "data-test", "data-qa", "name", "aria-label", "placeholder"];
  const MESSAGE_SELECTORS = [
    "[data-message-author-role]", '[data-testid*="message"]', ".message", '[class*="message"]',
    ".markdown", ".prose", '[class*="bubble"]', '[class*="response"]',
  ];

  function isOwn(el) {
    for (let n = el; n; n = n.parentElement) {
      if (n.id && n.id.indexOf(OWN) === 0) return true;
    }
    return false;
  }

  // An id is "stable" unless it looks machine-made: long digit runs, React ":r1a:", Ember.
  function stableId(id) {
    return !!id && id.length <= 64 && !/\s/.test(id) && !/\d{4,}/.test(id) && !/^:r/.test(id) && !/ember/i.test(id);
  }

  function matches(sel) {
    try { return Array.from(document.querySelectorAll(sel)); } catch (e) { return []; }
  }

  function isUnique(sel, el) {
    const found = matches(sel);
    return found.length === 1 && found[0] === el;
  }

  function quote(v) {
    return '"' + v.replace(/\\/g, "\\\\").replace(/"/g, '\\"') + '"';
  }

  function visible(el) {
    if (isOwn(el)) return false;
    const r = el.getBoundingClientRect();
    if (r.width < 8 || r.height < 8) return false;
    const s = getComputedStyle(el);
    if (s.visibility === "hidden" || s.display === "none" || parseFloat(s.opacity) === 0) return false;
    return r.bottom > 0 && r.right > 0 && r.top < innerHeight && r.left < innerWidth;
  }

  function pathTo(el) {
    const parts = [];
    let node = el;
    while (node && node.nodeType === 1 && node !== document.documentElement) {
      if (stableId(node.id) && isUnique("#" + CSS.escape(node.id), node)) {
        parts.unshift("#" + CSS.escape(node.id));
        break;
      }
      const tag = node.tagName.toLowerCase();
      if (tag === "body") { parts.unshift("body"); break; }
      const same = node.parentElement ? Array.from(node.parentElement.children).filter((c) => c.tagName === node.tagName) : [node];
      parts.unshift(same.length > 1 ? tag + ":nth-of-type(" + (same.indexOf(node) + 1) + ")" : tag);
      node = node.parentElement;
    }
    return parts.join(" > ");
  }

  // Preference: stable unique id, then a unique attribute, then a position path.
  function build(el) {
    if (stableId(el.id)) {
      const s = "#" + CSS.escape(el.id);
      if (isUnique(s, el)) return s;
    }
    const tag = el.tagName.toLowerCase();
    for (const a of ATTRS) {
      const v = el.getAttribute(a);
      if (!v || v.length > 80 || /[\n\r]/.test(v)) continue;
      for (const s of ["[" + a + "=" + quote(v) + "]", tag + "[" + a + "=" + quote(v) + "]"]) {
        if (isUnique(s, el)) return s;
      }
    }
    return pathTo(el);
  }

  // A visible Stop / Abort / Cancel-generation button means the site is still writing the reply.
  const STOP_RE = /stop|abort|cancel.?generat/i;
  function findStop() {
    const buttons = Array.from(document.querySelectorAll("button, [role='button']"));
    return buttons.find((b) => {
      if (!visible(b)) return false;
      const label = (b.getAttribute("aria-label") || "") + " " + (b.getAttribute("data-testid") || "");
      return STOP_RE.test(label) || /^stop/i.test((b.innerText || "").trim());
    }) || null;
  }

  // Best-effort: a visible CAPTCHA challenge, or a limit message in a dialog or alert.
  const BLOCK_RE = /verify (that )?you(?:['\u2019]re| are|re) (a )?(human|person)|are you a (robot|human)|captcha|too many requests|rate limit|usage limit|reached (the |your )?(message |usage )?(limit|cap)/i;
  function blocked() {
    const frames = Array.from(document.querySelectorAll(
      'iframe[src*="hcaptcha" i], iframe[src*="turnstile" i], iframe[src*="challenges.cloudflare.com" i], iframe[src*="recaptcha/api2/bframe" i]'));
    if (frames.some((f) => visible(f) && f.getBoundingClientRect().width >= 200)) return "captcha";
    const notes = Array.from(document.querySelectorAll("[role='dialog'], [role='alertdialog'], [role='alert']"));
    const hit = notes.find((n) => visible(n) && BLOCK_RE.test((n.innerText || "").slice(0, 400)));
    if (!hit) return "";
    return /captcha|human|robot/i.test(hit.innerText) ? "captcha" : "limit";
  }

  return { build, stableId, isUnique, isOwn, visible, matches, findStop, blocked, MESSAGE_SELECTORS };
})();
