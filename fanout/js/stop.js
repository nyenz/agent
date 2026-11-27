// Press the site's own Stop button, if one is showing. Returns true if it was pressed.
const stop = FanOutSel.findStop();
if (stop) { stop.click(); return true; }
return false;
