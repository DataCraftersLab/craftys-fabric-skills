/* DC documentation runtime. Vanilla, no dependencies, no network. */
(function () {
  'use strict';

  var SVG = 'http://www.w3.org/2000/svg';

  var PATHS = {
    moon: '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    sun:
      '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2' +
      'M4.93 4.93l1.41 1.41M17.66 17.66l1.41 1.41M6.34 17.66l-1.41 1.41M19.07 4.93l-1.41 1.41"/>',
    search: '<circle cx="11" cy="11" r="8"/><path d="M21 21l-4.35-4.35"/>',
    x: '<path d="M18 6 6 18M6 6l12 12"/>',
    copy:
      '<rect x="9" y="9" width="13" height="13" rx="2"/>' +
      '<path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
    check: '<path d="M20 6 9 17l-5-5"/>',
    list: '<path d="M8 6h13M8 12h13M8 18h13M3 6h.01M3 12h.01M3 18h.01"/>',
    up: '<path d="M12 19V5M5 12l7-7 7 7"/>',
    sections:
      '<path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z"/>' +
      '<path d="M14 2v6h6M16 13H8M16 17H8"/>',
    clock: '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
  };

  function icon(name, extra) {
    var el = document.createElementNS(SVG, 'svg');
    el.setAttribute('viewBox', '0 0 24 24');
    el.setAttribute('aria-hidden', 'true');
    el.setAttribute('class', 'dc-icon' + (extra ? ' ' + extra : ''));
    el.innerHTML = PATHS[name] || '';
    return el;
  }

  function slug(text) {
    return (
      text
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '') || 'section'
    );
  }

  /* ---------------------------------------------------------------- Theme */
  /* Precedence: the reader's own stored choice, then the host's
     data-appearance, then light. prefers-color-scheme is deliberately not
     consulted -- a DC document opens light unless someone said otherwise. */

  var THEME_KEY = 'dc-doc:theme';
  var isDark = false;

  function readStored() {
    try {
      return localStorage.getItem(THEME_KEY);
    } catch (err) {
      return null;
    }
  }

  function applyTheme() {
    document.documentElement.classList.toggle('dark', isDark);
    var btn = document.querySelector('[data-dc-theme]');
    if (!btn) return;
    btn.textContent = '';
    btn.appendChild(icon(isDark ? 'sun' : 'moon'));
    btn.setAttribute('aria-label', isDark ? 'Switch to light theme' : 'Switch to dark theme');
    btn.title = btn.getAttribute('aria-label');
  }

  function initTheme() {
    var stored = readStored();
    if (stored === 'dark') isDark = true;
    else if (stored === 'light') isDark = false;
    else isDark = document.documentElement.getAttribute('data-appearance') === 'dark';
    applyTheme();

    var btn = document.querySelector('[data-dc-theme]');
    if (!btn) return;
    btn.addEventListener('click', function () {
      isDark = !isDark;
      try {
        localStorage.setItem(THEME_KEY, isDark ? 'dark' : 'light');
      } catch (err) {
        /* file:// with storage blocked still toggles, it just forgets. */
      }
      applyTheme();
    });
  }

  /* ------------------------------------------------------- Headings + TOC */

  var headings = [];
  var tocLinks = [];

  function initHeadings(prose) {
    var used = Object.create(null);
    var nodes = prose.querySelectorAll('h2, h3, h4');
    Array.prototype.forEach.call(nodes, function (h) {
      if (h.hasAttribute('data-dc-no-anchor')) return;
      var id = h.id;
      if (!id) {
        id = slug(h.textContent);
        if (used[id]) id = id + '-' + ++used[id];
        else used[id] = 1;
        h.id = id;
      } else {
        used[id] = 1;
      }
      var a = document.createElement('a');
      a.className = 'dc-anchor';
      a.href = '#' + id;
      a.textContent = '#';
      a.setAttribute('aria-label', 'Link to this section');
      h.appendChild(a);
      if (h.tagName === 'H2' || h.tagName === 'H3') headings.push(h);
    });
  }

  /* The outline goes wherever the page put its slot: [data-dc-outline] nests it
     under the current page's link in a doc-set rail, [data-dc-toc] is the
     standalone-page rail. Same list either way. */
  function initToc(prose) {
    var slot = document.querySelector('[data-dc-outline]');
    var toc = slot || document.querySelector('[data-dc-toc]');
    var nested = !!slot;
    if (!toc) return;
    if (!headings.length) {
      var empty = document.createElement('p');
      empty.className = nested ? 'dc-nav-empty' : 'dc-toc-empty';
      empty.textContent = 'No sections yet.';
      toc.appendChild(empty);
      return;
    }
    var list = nested ? toc : document.createElement('ol');
    if (nested) list.className = 'dc-nav-outline';
    headings.forEach(function (h) {
      var li = document.createElement('li');
      li.className = 'lvl-' + h.tagName.slice(1);
      var a = document.createElement('a');
      a.href = '#' + h.id;
      a.textContent = h.textContent.replace(/#$/, '').trim();
      a.addEventListener('click', function () {
        document.body.removeAttribute('data-toc-open');
      });
      li.appendChild(a);
      list.appendChild(li);
      tocLinks.push(a);
    });
    if (!nested) toc.appendChild(list);

    var trigger = document.querySelector('[data-dc-toc-trigger]');
    if (trigger) {
      trigger.addEventListener('click', function () {
        var open = document.body.getAttribute('data-toc-open') === 'true';
        if (open) document.body.removeAttribute('data-toc-open');
        else document.body.setAttribute('data-toc-open', 'true');
      });
    }
    prose.addEventListener('click', function () {
      document.body.removeAttribute('data-toc-open');
    });
  }

  /* Deterministic spy: the active section is the last heading above a reading line
     that sweeps from under the sticky panel to the foot of the document as you
     scroll. An IntersectionObserver gets this wrong for sections shorter than the
     viewport, where two are on screen at once. */
  function initSpy() {
    if (!tocLinks.length) return;
    var panel = document.querySelector('.dc-panel');
    var box = document.querySelector('.dc-nav') || document.querySelector('[data-dc-toc]');
    var queued = false;

    function update() {
      queued = false;
      /* A reading line that sweeps from just under the panel to the foot of the
         document as you scroll, rather than sitting at a fixed height.

         A fixed line cannot reach a section that begins inside the last viewport:
         the page runs out of scroll before the heading gets there, so on a short
         page the middle sections were unreachable and the rail jumped straight from
         the first to the last. Tying the line to scroll progress guarantees every
         section has a range, and it lands on the last one exactly at the bottom --
         which is why no separate end-of-page special case is needed. */
      var offset = (panel ? panel.getBoundingClientRect().height : 60) + 12;
      var span = document.documentElement.scrollHeight - window.innerHeight;
      var progress = span > 4 ? Math.min(1, Math.max(0, window.scrollY / span)) : 0;
      var line = offset + (window.innerHeight - offset) * progress;

      var active = 0;
      for (var i = 0; i < headings.length; i++) {
        if (headings[i].getBoundingClientRect().top <= line) active = i;
        else break;
      }
      tocLinks.forEach(function (a, i) {
        if (i === active) a.setAttribute('aria-current', 'true');
        else a.removeAttribute('aria-current');
      });
      var current = tocLinks[active];
      if (current && box && box.scrollHeight > box.clientHeight) {
        var top = current.offsetTop;
        if (top < box.scrollTop || top > box.scrollTop + box.clientHeight - 40) {
          box.scrollTop = top - box.clientHeight / 2;
        }
      }
    }

    window.addEventListener(
      'scroll',
      function () {
        if (queued) return;
        queued = true;
        requestAnimationFrame(update);
      },
      { passive: true }
    );
    window.addEventListener('resize', update);
    update();
  }

  /* ------------------------------------------------------------- Doc-set nav */
  /* Folders are <details>, so they collapse without this. What the script adds:
     the folder holding the current page is opened, and every other folder's
     open state follows the reader across pages. */

  var NAV_KEY = 'dc-doc:nav-open';

  function readOpen() {
    try {
      return JSON.parse(localStorage.getItem(NAV_KEY)) || null;
    } catch (err) {
      return null;
    }
  }

  function initNav() {
    var nav = document.querySelector('.dc-nav');
    if (!nav) return;

    var groups = nav.querySelectorAll('.dc-nav-group');
    if (!groups.length) return;

    var current = nav.querySelector('[aria-current="page"]');
    var holder = current && current.closest ? current.closest('.dc-nav-group') : null;
    if (holder) holder.open = true;

    var stored = readOpen();

    /* The label only, never the page count beside it: the count changes whenever a
       page is added to the group, and keying on it would reset the reader's open
       folders on the next build. */
    function key(group) {
      var summary = group.querySelector('summary');
      if (!summary) return '';
      var text = '';
      Array.prototype.forEach.call(summary.childNodes, function (node) {
        if (node.nodeType === 1 && node.classList.contains('dc-nav-group-count')) return;
        text += node.textContent;
      });
      return text.trim();
    }

    if (stored) {
      Array.prototype.forEach.call(groups, function (group) {
        /* Never close the folder the reader is currently inside. */
        if (group === holder) return;
        group.open = stored.indexOf(key(group)) !== -1;
      });
    }

    function save() {
      var open = [];
      Array.prototype.forEach.call(groups, function (group) {
        if (group.open) open.push(key(group));
      });
      try {
        localStorage.setItem(NAV_KEY, JSON.stringify(open));
      } catch (err) {
        /* storage blocked on file:// -- folders still open, they just forget */
      }
    }

    Array.prototype.forEach.call(groups, function (group) {
      group.addEventListener('toggle', save);
    });

    /* A long doc set scrolls the rail; open on the page you are reading. */
    if (current && nav.scrollHeight > nav.clientHeight) {
      var top = current.offsetTop;
      if (top > nav.clientHeight - 80) nav.scrollTop = top - nav.clientHeight / 2;
    }
  }

  /* ------------------------------------------------------------- Code copy */

  function initCopy() {
    var blocks = document.querySelectorAll('.dc-code');
    Array.prototype.forEach.call(blocks, function (block) {
      var pre = block.querySelector('pre');
      if (!pre || block.hasAttribute('data-dc-no-copy')) return;
      var btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'dc-copy';
      var label = document.createElement('span');
      label.textContent = 'Copy';
      btn.appendChild(icon('copy', 's'));
      btn.appendChild(label);
      btn.addEventListener('click', function () {
        var text = pre.innerText;
        var done = function () {
          btn.setAttribute('data-done', 'true');
          btn.textContent = '';
          btn.appendChild(icon('check', 's'));
          var ok = document.createElement('span');
          ok.textContent = 'Copied';
          btn.appendChild(ok);
          setTimeout(function () {
            btn.removeAttribute('data-done');
            btn.textContent = '';
            btn.appendChild(icon('copy', 's'));
            var again = document.createElement('span');
            again.textContent = 'Copy';
            btn.appendChild(again);
          }, 1600);
        };
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(text).then(done, fallback);
        } else {
          fallback();
        }
        function fallback() {
          var ta = document.createElement('textarea');
          ta.value = text;
          ta.style.position = 'fixed';
          ta.style.opacity = '0';
          document.body.appendChild(ta);
          ta.select();
          try {
            document.execCommand('copy');
            done();
          } catch (err) {
            /* nothing sensible left to try */
          }
          document.body.removeChild(ta);
        }
      });
      var head = block.querySelector('.dc-code-head');
      if (head) head.appendChild(btn);
      else block.appendChild(btn);
    });
  }

  /* ---------------------------------------------------------------- Search */

  function initSearch(sheet) {
    var input = document.querySelector('[data-dc-search]');
    if (!input) return;
    var countEl = document.querySelector('[data-dc-search-count]');
    var clearEl = document.querySelector('[data-dc-search-clear]');
    var hits = [];
    var cur = -1;
    var timer = null;

    function setCount() {
      if (!countEl) return;
      if (!hits.length) countEl.textContent = input.value.trim().length >= 2 ? '0' : '';
      else if (cur < 0) countEl.textContent = String(hits.length);
      else countEl.textContent = cur + 1 + '/' + hits.length;
    }

    function clearHits() {
      hits.forEach(function (m) {
        var parent = m.parentNode;
        if (!parent) return;
        parent.replaceChild(document.createTextNode(m.textContent), m);
        parent.normalize();
      });
      hits = [];
      cur = -1;
    }

    function markNode(node, needle) {
      var lower = node.nodeValue.toLowerCase();
      var at = 0;
      var starts = [];
      while ((at = lower.indexOf(needle, at)) !== -1) {
        starts.push(at);
        at += needle.length;
      }
      var made = [];
      /* Right to left, so the offsets computed above stay valid as we split. */
      for (var i = starts.length - 1; i >= 0; i--) {
        var match = node.splitText(starts[i]);
        match.splitText(needle.length);
        var mark = document.createElement('mark');
        mark.className = 'dc-hit';
        mark.appendChild(document.createTextNode(match.nodeValue));
        match.parentNode.replaceChild(mark, match);
        made.unshift(mark);
      }
      return made;
    }

    function run() {
      clearHits();
      var query = input.value.trim();
      if (query.length < 2) {
        setCount();
        return;
      }
      var needle = query.toLowerCase();
      var walker = document.createTreeWalker(sheet, NodeFilter.SHOW_TEXT, {
        acceptNode: function (node) {
          var name = node.parentNode ? node.parentNode.nodeName : '';
          if (name === 'SCRIPT' || name === 'STYLE' || name === 'MARK') {
            return NodeFilter.FILTER_REJECT;
          }
          /* Never mark inside an SVG. A hit is wrapped in <mark>, which is not an
             SVG element, so marking a <text> node silently erases the label -- and
             a diagram's labels are exactly the words worth searching for. */
          if (node.parentNode && node.parentNode.ownerSVGElement) {
            return NodeFilter.FILTER_REJECT;
          }
          if (node.nodeValue.toLowerCase().indexOf(needle) === -1) {
            return NodeFilter.FILTER_REJECT;
          }
          return NodeFilter.FILTER_ACCEPT;
        },
      });
      var targets = [];
      while (walker.nextNode()) targets.push(walker.currentNode);
      targets.forEach(function (node) {
        hits = hits.concat(markNode(node, needle));
      });
      setCount();
    }

    function jump(step) {
      if (!hits.length) return;
      if (cur >= 0) hits[cur].removeAttribute('data-current');
      cur = (cur + step + hits.length) % hits.length;
      var mark = hits[cur];
      mark.setAttribute('data-current', 'true');
      var details = mark.closest ? mark.closest('details') : null;
      if (details) details.open = true;
      mark.scrollIntoView({ block: 'center', behavior: 'smooth' });
      setCount();
    }

    input.addEventListener('input', function () {
      if (timer) clearTimeout(timer);
      timer = setTimeout(run, 140);
    });

    input.addEventListener('keydown', function (event) {
      if (event.key === 'Enter') {
        event.preventDefault();
        if (timer) {
          clearTimeout(timer);
          timer = null;
          run();
        }
        jump(event.shiftKey ? -1 : 1);
      } else if (event.key === 'ArrowDown') {
        event.preventDefault();
        jump(1);
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        jump(-1);
      } else if (event.key === 'Escape') {
        input.value = '';
        clearHits();
        setCount();
        input.blur();
      }
    });

    if (clearEl) {
      clearEl.addEventListener('click', function () {
        input.value = '';
        clearHits();
        setCount();
        input.focus();
      });
    }

    document.addEventListener('keydown', function (event) {
      if (event.target === input) return;
      var typing = /^(INPUT|TEXTAREA|SELECT)$/.test(event.target.nodeName);
      if ((event.key === '/' && !typing) || (event.key === 'k' && (event.ctrlKey || event.metaKey))) {
        event.preventDefault();
        input.focus();
        input.select();
      }
      if (event.key === 'Escape') document.body.removeAttribute('data-toc-open');
    });
  }

  /* ----------------------------------------------------------- Panel stats */

  function initStats(prose) {
    var slot = document.querySelector('[data-dc-stats]');
    if (!slot || slot.children.length) return;
    var words = (prose.textContent.trim().match(/\S+/g) || []).length;
    var minutes = Math.max(1, Math.round(words / 220));
    var sections = prose.querySelectorAll('h2').length;

    [
      { name: 'sections', label: 'Sections', value: sections },
      { name: 'clock', label: 'Read', value: minutes + ' min' },
    ].forEach(function (stat) {
      if (!stat.value) return;
      var wrap = document.createElement('div');
      wrap.className = 'dc-stat';
      wrap.appendChild(icon(stat.name));
      var label = document.createElement('span');
      label.className = 'dc-stat-label';
      label.textContent = stat.label;
      var value = document.createElement('span');
      value.className = 'dc-stat-value';
      value.textContent = String(stat.value);
      wrap.appendChild(label);
      wrap.appendChild(value);
      slot.appendChild(wrap);
    });
  }

  /* ------------------------------------------------------------ Back to top */

  function initToTop() {
    var btn = document.querySelector('[data-dc-top]');
    if (!btn) return;
    btn.appendChild(icon('up'));
    btn.addEventListener('click', function () {
      window.scrollTo({ top: 0, behavior: 'smooth' });
    });
    var queued = false;
    function update() {
      queued = false;
      btn.setAttribute('data-visible', window.scrollY > 600 ? 'true' : 'false');
    }
    window.addEventListener(
      'scroll',
      function () {
        if (queued) return;
        queued = true;
        requestAnimationFrame(update);
      },
      { passive: true }
    );
    update();
  }

  /* -------------------------------------------------------------- Boot */

  function initChrome() {
    var brand = document.querySelector('[data-dc-logo]');
    if (brand && window.DC_LOGO) brand.innerHTML = window.DC_LOGO;

    var searchIcon = document.querySelector('[data-dc-search-icon]');
    if (searchIcon && !searchIcon.childNodes.length) searchIcon.appendChild(icon('search'));

    var clearIcon = document.querySelector('[data-dc-search-clear]');
    if (clearIcon && !clearIcon.childNodes.length) clearIcon.appendChild(icon('x', 's'));

    var trigger = document.querySelector('[data-dc-toc-trigger]');
    if (trigger && !trigger.childNodes.length) {
      trigger.appendChild(icon('list'));
      var text = document.createElement('span');
      text.textContent = 'Contents';
      trigger.appendChild(text);
    }
  }

  function initTabs() {
    var groups = document.querySelectorAll('[data-dc-tabs]');
    Array.prototype.forEach.call(groups, function (group) {
      var tabs = group.querySelectorAll('.dc-tab');
      Array.prototype.forEach.call(tabs, function (tab) {
        tab.addEventListener('click', function () {
          Array.prototype.forEach.call(tabs, function (other) {
            var panel = document.getElementById(other.getAttribute('aria-controls'));
            var on = other === tab;
            other.setAttribute('aria-selected', on ? 'true' : 'false');
            if (panel) panel.hidden = !on;
          });
        });
      });
    });
  }

  function boot() {
    var prose = document.querySelector('.dc-prose');
    var sheet = document.querySelector('.dc-sheet') || prose;
    initChrome();
    initTheme();
    initToTop();
    initTabs();
    if (!prose) return;
    initHeadings(prose);
    initToc(prose);
    initNav();
    initSpy();
    initCopy();
    initStats(prose);
    if (sheet) initSearch(sheet);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
