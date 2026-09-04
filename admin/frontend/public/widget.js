/*
 * The chat bubble, for somebody else's website.
 *
 *   <script src="https://console.example.com/widget.js"
 *           data-key="wk_..." async></script>
 *
 * Everything lives in a shadow root. This lands on sites whose CSS we have
 * never seen: a stray `button { width: 100% }` would wreck the panel, and our
 * own styles leaking out would be worse. Shadow DOM is the only thing that
 * makes either promise keepable.
 *
 * No framework, no build step, no dependencies. It is served as written, and
 * the whole file is what a customer's page has to download before anybody has
 * asked a question.
 */
;(function () {
  var script = document.currentScript
  if (!script) return
  var key = script.getAttribute('data-key')
  if (!key) {
    console.warn('[chat widget] no data-key on the script tag')
    return
  }

  // Where the API is, taken from where this file came from. Hard-coding it
  // would mean a different snippet per environment, and the snippet is the one
  // thing a customer copies by hand.
  //
  // /api because that is the only path the console's nginx proxies through;
  // anything else answers with the single-page app, which arrives here as a
  // parse error rather than as a 404.
  var api = new URL(script.src).origin + '/api'

  // Per browser, so a returning visitor keeps their conversation. sessionStorage
  // and not localStorage: a shared machine should not hand the next person the
  // last person's chat.
  var SESSION = 'aivoice.chat.sid'
  var sid = sessionStorage.getItem(SESSION)
  if (!sid) {
    sid = 'w' + Math.random().toString(36).slice(2) + Date.now().toString(36)
    sessionStorage.setItem(SESSION, sid)
  }

  var host = document.createElement('div')
  host.style.cssText = 'position:fixed;bottom:0;right:0;z-index:2147483000'
  var root = host.attachShadow({ mode: 'open' })
  document.body.appendChild(host)

  root.innerHTML = [
    '<style>',
    ':host,*{box-sizing:border-box}',
    'button{font:inherit;cursor:pointer;border:0;background:none}',
    '.bubble{position:fixed;bottom:20px;right:20px;width:56px;height:56px;',
    '  border-radius:50%;background:#2563eb;color:#fff;display:grid;',
    '  place-items:center;box-shadow:0 6px 24px rgba(0,0,0,.25);font-size:24px}',
    '.panel{position:fixed;bottom:88px;right:20px;width:360px;max-width:calc(100vw - 40px);',
    '  height:520px;max-height:calc(100vh - 120px);background:#fff;color:#111;',
    '  border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.28);display:none;',
    '  flex-direction:column;overflow:hidden;',
    '  font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}',
    '.panel.open{display:flex}',
    '.head{display:flex;align-items:center;justify-content:space-between;',
    '  padding:12px 14px;background:#2563eb;color:#fff;font-weight:600}',
    '.head button{color:#fff;font-size:20px;line-height:1}',
    '.log{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px}',
    '.msg{max-width:85%;padding:8px 11px;border-radius:12px;white-space:pre-wrap;',
    '  word-wrap:break-word}',
    '.them{background:#f1f5f9;align-self:flex-start;border-bottom-left-radius:4px}',
    '.me{background:#2563eb;color:#fff;align-self:flex-end;border-bottom-right-radius:4px}',
    '.dots{align-self:flex-start;color:#64748b;font-size:13px;padding:4px 2px}',
    '.foot{display:flex;gap:8px;padding:10px;border-top:1px solid #e2e8f0}',
    '.foot input{flex:1;padding:9px 11px;border:1px solid #cbd5e1;border-radius:8px;',
    '  font:inherit;outline:none;min-width:0}',
    '.foot input:focus{border-color:#2563eb}',
    '.foot button{background:#2563eb;color:#fff;border-radius:8px;padding:0 14px}',
    '.foot button:disabled{opacity:.5;cursor:default}',
    '</style>',
    '<button class="bubble" aria-label="Chat">&#128172;</button>',
    '<div class="panel" role="dialog" aria-label="Chat">',
    '  <div class="head"><span class="title">Chat</span>',
    '    <button class="close" aria-label="Close">&times;</button></div>',
    '  <div class="log"></div>',
    '  <form class="foot">',
    '    <input class="in" autocomplete="off" placeholder="Type a message…">',
    '    <button type="submit" class="go">&#10148;</button>',
    '  </form>',
    '</div>',
  ].join('')

  var $ = function (sel) {
    return root.querySelector(sel)
  }
  var panel = $('.panel')
  var log = $('.log')
  var input = $('.in')
  var go = $('.go')

  function say(text, mine) {
    var el = document.createElement('div')
    el.className = 'msg ' + (mine ? 'me' : 'them')
    el.textContent = text
    log.appendChild(el)
    log.scrollTop = log.scrollHeight
    return el
  }

  var started = false
  function open() {
    panel.classList.add('open')
    input.focus()
    if (started) return
    started = true
    // Config is fetched on first open, not on page load. A widget nobody
    // clicks should cost the host page nothing but this file.
    fetch(api + '/widget/' + encodeURIComponent(key) + '/config')
      .then(function (r) {
        if (!r.ok) throw new Error(r.status)
        return r.json()
      })
      .then(function (c) {
        $('.title').textContent = c.title || 'Chat'
        if (c.welcome) say(c.welcome, false)
      })
      .catch(function () {
        // The visitor did not install this and cannot fix it. They get a
        // sentence; the console operator gets the refusal in the API log.
        say('Chat is not available right now.', false)
        input.disabled = true
        go.disabled = true
      })
  }

  $('.bubble').addEventListener('click', function () {
    panel.classList.contains('open') ? panel.classList.remove('open') : open()
  })
  $('.close').addEventListener('click', function () {
    panel.classList.remove('open')
  })

  var busy = false
  $('.foot').addEventListener('submit', function (e) {
    e.preventDefault()
    var text = input.value.trim()
    if (!text || busy) return
    input.value = ''
    say(text, true)

    busy = true
    go.disabled = true
    var dots = document.createElement('div')
    dots.className = 'dots'
    dots.textContent = '…'
    log.appendChild(dots)
    log.scrollTop = log.scrollHeight

    fetch(api + '/widget/' + encodeURIComponent(key) + '/chat', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      // No history: the server holds it. A client that sends its own can send
      // one where the agent has already agreed to something.
      body: JSON.stringify({ session_id: sid, message: text }),
    })
      .then(function (r) {
        return r.ok ? r.json() : { text: 'Sorry, something went wrong. Please try again.' }
      })
      .catch(function () {
        return { text: 'Sorry, I could not reach the server. Please try again.' }
      })
      .then(function (d) {
        dots.remove()
        say(d.text || '…', false)
        busy = false
        go.disabled = false
        input.focus()
      })
  })
})()
