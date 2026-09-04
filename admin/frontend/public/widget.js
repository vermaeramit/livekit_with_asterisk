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
    // Everything colourable reads one variable, set once from the campaign's
    // accent. Two places to change it is how a header and a button end up
    // different shades of the same brand.
    ':host{--accent:#2563eb;--on-accent:#fff}',
    ':host,*{box-sizing:border-box}',
    'button{font:inherit;cursor:pointer;border:0;background:none}',
    '.bubble{position:fixed;bottom:20px;right:20px;width:56px;height:56px;',
    '  border-radius:50%;background:var(--accent);color:var(--on-accent);display:grid;',
    '  place-items:center;box-shadow:0 6px 24px rgba(0,0,0,.25);font-size:24px}',
    '.panel{position:fixed;bottom:88px;right:20px;width:360px;max-width:calc(100vw - 40px);',
    '  height:520px;max-height:calc(100vh - 120px);background:#fff;color:#111;',
    '  border-radius:14px;box-shadow:0 12px 40px rgba(0,0,0,.28);display:none;',
    '  flex-direction:column;overflow:hidden;',
    '  font:14px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}',
    '.panel.open{display:flex}',
    '.head{display:flex;align-items:center;justify-content:space-between;',
    '  padding:12px 14px;background:var(--accent);color:var(--on-accent);font-weight:600}',
    '.head button{color:var(--on-accent);font-size:20px;line-height:1}',
'.head .brand{display:flex;align-items:center;gap:8px;min-width:0}',
'.head img{width:20px;height:20px;border-radius:4px;object-fit:contain;background:#fff}',
'.bubble img{width:28px;height:28px;border-radius:6px;object-fit:contain}',
    '.log{flex:1;overflow-y:auto;padding:14px;display:flex;flex-direction:column;gap:10px}',
    '.msg{max-width:85%;padding:8px 11px;border-radius:12px;white-space:pre-wrap;',
    '  word-wrap:break-word}',
    '.them{background:#f1f5f9;align-self:flex-start;border-bottom-left-radius:4px}',
    '.me{background:var(--accent);color:var(--on-accent);align-self:flex-end;',
'  border-bottom-right-radius:4px}',
    // Three dots that actually move. A static "…" reads as a message the
    // agent sent, which is the opposite of what it means.
    '.dots{align-self:flex-start;display:flex;gap:4px;padding:10px 12px;',
    '  background:#f1f5f9;border-radius:12px;border-bottom-left-radius:4px}',
    '.dots i{width:6px;height:6px;border-radius:50%;background:#94a3b8;',
    '  animation:blink 1.2s infinite ease-in-out}',
    '.dots i:nth-child(2){animation-delay:.2s}',
    '.dots i:nth-child(3){animation-delay:.4s}',
    '@keyframes blink{0%,80%,100%{opacity:.25;transform:translateY(0)}',
    '  40%{opacity:1;transform:translateY(-3px)}}',
    '.foot{display:flex;gap:8px;padding:10px;border-top:1px solid #e2e8f0}',
    '.foot input{flex:1;padding:9px 11px;border:1px solid #cbd5e1;border-radius:8px;',
    '  font:inherit;outline:none;min-width:0}',
    '.foot input:focus{border-color:var(--accent)}',
    '.foot button{background:var(--accent);color:var(--on-accent);border-radius:8px;padding:0 14px}',
    '.foot button:disabled{opacity:.5;cursor:default}',
    '</style>',
    '<button class="bubble" aria-label="Chat">&#128172;</button>',
    '<div class="panel" role="dialog" aria-label="Chat">',
    '  <div class="head"><span class="brand"><span class="ico"></span>',
    '    <span class="title">Chat</span></span>',
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

  /*
   * The accent, and the text colour that has to sit on it.
   *
   * The text colour is COMPUTED, never configured. A stored one can disagree
   * with the accent - pick a pale yellow and leave the text white, and the
   * header becomes unreadable with both fields looking filled in. The formula
   * is the standard relative-luminance one; anything above the midpoint gets
   * dark text.
   */
  function paint(hex) {
    if (!/^#[0-9a-fA-F]{6}$/.test(hex)) return
    var n = parseInt(hex.slice(1), 16)
    var c = [(n >> 16) & 255, (n >> 8) & 255, n & 255].map(function (v) {
      v /= 255
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4)
    })
    var lum = 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    root.host.style.setProperty('--accent', hex)
    root.host.style.setProperty('--on-accent', lum > 0.45 ? '#111' : '#fff')
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
        if (c.accent) paint(c.accent)
        if (c.icon) {
          // A path from the server, resolved against the address this script
          // came from - the one thing it can be sure of. An absolute URL built
          // on the server would depend on proxy headers being right.
          var iconSrc = c.icon.charAt(0) === '/' ? api + c.icon : c.icon
          // Two copies of the same logo: one in the header, one replacing the
          // speech-bubble glyph. A brand mark on a coloured circle is what a
          // visitor recognises before they read anything.
          var head = document.createElement('img')
          head.src = iconSrc
          head.alt = ''
          $('.ico').appendChild(head)
          var big = document.createElement('img')
          big.src = iconSrc
          big.alt = ''
          var b = $('.bubble')
          b.textContent = ''
          b.appendChild(big)
        }
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
    dots.innerHTML = '<i></i><i></i><i></i>'
    log.appendChild(dots)
    log.scrollTop = log.scrollHeight

    var bubble = null
    function write(chunk) {
      if (!bubble) {
        // The indicator is replaced by the answer the moment there IS one, so
        // the panel never shows both.
        dots.remove()
        bubble = say('', false)
      }
      bubble.textContent += chunk
      log.scrollTop = log.scrollHeight
    }

    function finish() {
      if (dots.parentNode) dots.remove()
      if (!bubble) say('…', false)
      busy = false
      go.disabled = false
      input.focus()
    }

    fetch(api + '/widget/' + encodeURIComponent(key) + '/chat', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      // No history: the server holds it. A client that sends its own can send
      // one where the agent has already agreed to something.
      body: JSON.stringify({ session_id: sid, message: text }),
    })
      .then(function (r) {
        if (!r.ok || !r.body) throw new Error(r.status)
        // Newline-delimited JSON, read as it arrives. The tail of the buffer
        // is usually half a line and has to wait for the rest.
        var reader = r.body.getReader()
        var decoder = new TextDecoder()
        var buf = ''

        return (function pump() {
          return reader.read().then(function (res) {
            if (res.done) return
            buf += decoder.decode(res.value, { stream: true })
            var nl
            while ((nl = buf.indexOf('\n')) !== -1) {
              var raw = buf.slice(0, nl).trim()
              buf = buf.slice(nl + 1)
              if (!raw) continue
              var msg
              try {
                msg = JSON.parse(raw)
              } catch (e) {
                continue
              }
              if (msg.delta) write(msg.delta)
              if (msg.done) {
                // The final text wins over what was streamed: a dropped frame
                // would otherwise leave a sentence half-written, and a
                // customer is reading it.
                if (msg.text && bubble) bubble.textContent = msg.text
                else if (msg.text) write(msg.text)
              }
            }
            return pump()
          })
        })()
      })
      .catch(function () {
        if (!bubble) {
          dots.remove()
          say('Sorry, I could not reach the server. Please try again.', false)
        }
      })
      .then(finish)
  })
})()
