/* Semantic Model Analyzer: the app layer over the DC doc theme.
 *
 * The page is still a document: with no script every section is on it, in order, and
 * every link is an anchor. This adds what makes it quick to use: a finder over every
 * object on every page, an inspector that opens one object without leaving the page,
 * one section at a time instead of one long scroll, and the keys to drive all three.
 * It reads the index island (#sma-index) the generator wrote; it never re-derives a fact.
 */
(function () {
  'use strict';

  var D = document;
  var W = window;
  var PAGE = D.body.getAttribute('data-sma-page') || '';
  var IX = { pages: {}, objects: [], findings: [], checks: [], views: [], cards: [] };
  var BY = Object.create(null);

  var KIND_LABEL = {
    all: 'All',
    measure: 'Measures',
    table: 'Tables',
    column: 'Columns',
    relationship: 'Relationships',
    query: 'Queries',
    role: 'Roles',
    'calc group': 'Calculation groups',
    hierarchy: 'Hierarchies',
    expression: 'Expressions',
    finding: 'Findings',
    check: 'AI checks',
    section: 'Sections',
  };
  var KIND_ORDER = ['measure', 'table', 'column', 'query', 'relationship', 'finding', 'section',
    'check', 'role', 'calc group', 'hierarchy', 'expression'];
  var KIND_WEIGHT = { section: 34, measure: 30, table: 30, query: 28, column: 20, finding: 16,
    check: 14, relationship: 10 };
  var STATUS = { pass: 'Pass', fail: 'Not yet', na: 'Not in this input' };

  function $(sel, root) {
    return (root || D).querySelector(sel);
  }
  function $$(sel, root) {
    return Array.prototype.slice.call((root || D).querySelectorAll(sel));
  }
  function el(tag, cls, text) {
    var n = D.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }
  function typing(t) {
    return t && (/^(INPUT|TEXTAREA|SELECT)$/.test(t.nodeName) || t.isContentEditable);
  }
  function store(key, val) {
    try {
      if (val === undefined) return JSON.parse(localStorage.getItem(key));
      localStorage.setItem(key, JSON.stringify(val));
    } catch (e) {
      return null;
    }
    return null;
  }
  function fileOf(page) {
    return (IX.pages[page] || {}).file || '';
  }
  function labelOf(page) {
    return (IX.pages[page] || {}).label || '';
  }

  function load() {
    var node = D.getElementById('sma-index');
    if (!node) return;
    try {
      IX = JSON.parse(node.textContent);
    } catch (e) {
      return;
    }
    IX.objects.forEach(function (o) {
      BY[o.id] = o;
    });
  }

  /* Go to a page and an anchor. On this page that is a hash change the router
     handles; on another page it is a load. */
  function go(page, anchor) {
    var file = fileOf(page);
    if (!file) return;
    if (page === PAGE) {
      if ('#' + anchor === location.hash) route();
      else location.hash = anchor;
    } else {
      location.href = file + '#' + anchor;
    }
  }

  /* ================================================================ Views ===== */

  var views = [];
  var tabs = [];
  var current = null;
  var allMode = false;
  var UNTIL = 'onbeforematch' in D.body;
  var allBtn = null;

  function stickyTop() {
    var panel = $('.dc-panel');
    var strip = $('.sma-tabs');
    return (panel ? panel.getBoundingClientRect().height : 70) +
      (strip && views.length ? strip.getBoundingClientRect().height : 0) + 12;
  }

  function hideView(v) {
    if (UNTIL) v.setAttribute('hidden', 'until-found');
    else v.hidden = true;
  }

  function markTabs(id) {
    tabs.forEach(function (t) {
      var on = t.getAttribute('data-view') === id;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on || (!id && t === tabs[0]) ? 0 : -1;
      t.classList.toggle('on', on);
    });
    $$('.dc-nav-page.is-current .dc-nav-outline a').forEach(function (a) {
      if (id && a.getAttribute('href') === '#' + id) a.setAttribute('data-sma-current', 'true');
      else a.removeAttribute('data-sma-current');
    });
    if (allBtn) allBtn.setAttribute('aria-pressed', allMode ? 'true' : 'false');
  }

  function relayout() {
    try {
      W.dispatchEvent(new Event('resize'));
    } catch (e) {
      /* old engines: the widgets keep their first layout */
    }
  }

  function scrollToViews() {
    var strip = $('.sma-tabs');
    if (!strip) return;
    var panel = $('.dc-panel');
    var y = strip.getBoundingClientRect().top + W.scrollY -
      (panel ? panel.getBoundingClientRect().height : 70);
    if (W.scrollY > y + 1) W.scrollTo(0, y);
  }

  function show(id, keepScroll) {
    var v = null;
    for (var i = 0; i < views.length; i++) {
      if (views[i].getAttribute('data-view') === id) v = views[i];
    }
    if (!v) return false;
    allMode = false;
    D.body.removeAttribute('data-sma-all');
    views.forEach(function (x) {
      if (x === v) x.removeAttribute('hidden');
      else hideView(x);
    });
    current = id;
    markTabs(id);
    if (!keepScroll) scrollToViews();
    relayout();
    return true;
  }

  function showAll() {
    allMode = true;
    D.body.setAttribute('data-sma-all', 'true');
    views.forEach(function (x) {
      x.removeAttribute('hidden');
    });
    markTabs(null);
    relayout();
  }

  function navigate(id) {
    if (history.pushState) history.pushState(null, '', '#' + id);
    show(id);
  }

  function step(delta) {
    if (!views.length) return;
    var ids = views.map(function (v) {
      return v.getAttribute('data-view');
    });
    var at = allMode ? -1 : ids.indexOf(current);
    var next = Math.max(0, Math.min(ids.length - 1, at + delta));
    if (at === -1) next = delta > 0 ? 0 : ids.length - 1;
    navigate(ids[next]);
  }

  function flash(node) {
    node.classList.remove('sma-flash');
    void node.offsetWidth;
    node.classList.add('sma-flash');
    setTimeout(function () {
      node.classList.remove('sma-flash');
    }, 1600);
  }

  function reveal(node) {
    var y = node.getBoundingClientRect().top + W.scrollY - stickyTop();
    W.scrollTo(0, Math.max(0, y));
  }

  function route() {
    var raw = location.hash.slice(1);
    var hash = '';
    try {
      hash = decodeURIComponent(raw);
    } catch (e) {
      hash = raw;
    }
    if (hash.indexOf('obj=') === 0) {
      if (views.length && !current && !allMode) show(views[0].getAttribute('data-view'), true);
      openObj(hash.slice(4));
      return;
    }
    var q = hash.split('?');
    var target = q[0] ? D.getElementById(q[0]) : null;
    var pick = null;
    if (q[1]) {
      var m = /(?:^|&)pick=([^&]*)/.exec(q[1]);
      if (m) pick = m[1];
    }
    if (!target) {
      if (views.length && !current && !allMode) show(views[0].getAttribute('data-view'), true);
      return;
    }
    var v = target.closest ? target.closest('.sma-view') : null;
    if (v && !allMode) show(v.getAttribute('data-view'), true);
    for (var det = target.closest ? target.closest('details') : null; det;
      det = det.parentElement ? det.parentElement.closest('details') : null) {
      det.open = true;
    }
    var isHead = v && target.matches('.sma-view-head h2');
    if (pick) {
      var buttons = $$('[data-anat-pick]', target);
      for (var i = 0; i < buttons.length; i++) {
        if (buttons[i].getAttribute('data-anat-pick') === pick && !buttons[i].disabled) {
          var group = buttons[i].closest('details');
          if (group) group.open = true;
          buttons[i].click();
          break;
        }
      }
    }
    requestAnimationFrame(function () {
      if (isHead) scrollToViews();
      else reveal(target);
      if (!isHead) {
        flash(target);
        if (target.hasAttribute('tabindex')) target.focus({ preventScroll: true });
      }
    });
  }

  function initViews() {
    views = $$('.sma-view');
    if (!views.length) return;
    D.body.classList.add('sma-has-views');
    var strip = $('.sma-tabs-in');
    if (strip) strip.setAttribute('role', 'tablist');
    tabs = $$('.sma-tab[data-view]');
    tabs.forEach(function (t, i) {
      var id = t.getAttribute('data-view');
      t.setAttribute('role', 'tab');
      t.id = 'tab-' + id;
      t.setAttribute('aria-controls', 'view-' + id);
      var key = el('span', 'sma-tab-k', i < 9 ? String(i + 1) : '');
      key.setAttribute('aria-hidden', 'true');
      if (i < 9) t.insertBefore(key, t.firstChild);
      t.addEventListener('click', function (e) {
        e.preventDefault();
        navigate(id);
      });
      t.addEventListener('keydown', function (e) {
        var at = tabs.indexOf(t);
        var to = null;
        if (e.key === 'ArrowRight') to = tabs[(at + 1) % tabs.length];
        else if (e.key === 'ArrowLeft') to = tabs[(at - 1 + tabs.length) % tabs.length];
        else if (e.key === 'Home') to = tabs[0];
        else if (e.key === 'End') to = tabs[tabs.length - 1];
        if (to) {
          e.preventDefault();
          to.focus();
          navigate(to.getAttribute('data-view'));
        }
      });
    });
    views.forEach(function (v, i) {
      v.setAttribute('role', 'tabpanel');
      v.addEventListener('beforematch', function () {
        show(v.getAttribute('data-view'), true);
      });
      var pager = el('nav', 'sma-pager');
      pager.setAttribute('aria-label', 'Previous and next section');
      [[i - 1, 'prev'], [i + 1, 'next']].forEach(function (p) {
        var other = views[p[0]];
        if (!other) {
          pager.appendChild(el('span'));
          return;
        }
        var b = el('button', 'sma-pager-' + p[1]);
        b.type = 'button';
        b.appendChild(el('span', 'sma-pager-k', p[1] === 'prev' ? 'Previous' : 'Next'));
        b.appendChild(el('span', 'sma-pager-n', other.getAttribute('data-label')));
        b.addEventListener('click', function () {
          navigate(other.getAttribute('data-view'));
        });
        pager.appendChild(b);
      });
      v.appendChild(pager);
    });
    allBtn = $('[data-view-all]');
    if (allBtn) {
      allBtn.addEventListener('click', function () {
        if (allMode) {
          show(current || views[0].getAttribute('data-view'), true);
        } else {
          showAll();
        }
      });
    }
    W.addEventListener('hashchange', route);
    W.addEventListener('beforeprint', function () {
      if (!allMode) {
        D.body.setAttribute('data-sma-printed-from', current || '');
        showAll();
      }
    });
    W.addEventListener('afterprint', function () {
      var back = D.body.getAttribute('data-sma-printed-from');
      D.body.removeAttribute('data-sma-printed-from');
      if (back) show(back, true);
    });
  }

  /* KPI strip and overview tiles: a count opens the finder on that kind. */
  var KPI_KIND = { measures: 'measure', tables: 'table', columns: 'column',
    relationships: 'relationship', queries: 'query' };

  function initBrowse() {
    $$('.sma-kpis .dc-rail-stat').forEach(function (cell) {
      var label = ($('.dc-rail-label', cell) || {}).textContent || '';
      var kind = KPI_KIND[label.trim().toLowerCase()];
      if (!kind) return;
      cell.setAttribute('data-sma-browse', kind);
      cell.setAttribute('role', 'button');
      cell.setAttribute('tabindex', '0');
      cell.setAttribute('title', 'Browse ' + label.toLowerCase());
    });
    D.addEventListener('click', function (e) {
      var t = e.target.closest ? e.target.closest('[data-sma-browse],[data-sma-open],[data-sma-go]') : null;
      if (!t) return;
      if (t.hasAttribute('data-sma-browse')) openFinder(t.getAttribute('data-sma-browse'));
      else if (t.hasAttribute('data-sma-open')) openObj(t.getAttribute('data-sma-open'));
      else location.href = t.getAttribute('data-sma-go');
    });
    D.addEventListener('keydown', function (e) {
      var t = e.target;
      if ((e.key === 'Enter' || e.key === ' ') && t.getAttribute &&
          t.getAttribute('role') === 'button' && t.hasAttribute('data-sma-browse')) {
        e.preventDefault();
        openFinder(t.getAttribute('data-sma-browse'));
      }
    });
  }

  /* =============================================================== Finder ===== */

  var input, pop, list, kindsEl, live;
  var universe = [];
  var kind = 'all';
  var results = [];
  var active = -1;
  var returnFocus = null;

  function buildUniverse() {
    var out = [];
    IX.objects.forEach(function (o) {
      out.push({ t: 'obj', k: o.k, n: o.n, s: o.s || '', id: o.id,
        hay: (o.n + ' ' + (o.full || '') + ' ' + (o.s || '')).toLowerCase() });
    });
    IX.findings.forEach(function (f, i) {
      out.push({ t: 'finding', k: 'finding', n: f.subj || f.code, s: f.code + ' · ' + f.msg.replace(/`/g, ''),
        lv: f.lv, fi: i, hay: (f.subj + ' ' + f.code + ' ' + f.msg + ' ' + f.lv).toLowerCase() });
    });
    IX.checks.forEach(function (c) {
      out.push({ t: 'link', k: 'check', n: c.n, s: STATUS[c.st] + ' · ' + c.g, st: c.st,
        page: 'ai', anchor: 'chk-' + c.key, hay: (c.n + ' ' + c.g + ' ' + c.d).toLowerCase() });
    });
    Object.keys(IX.pages).forEach(function (p) {
      out.push({ t: 'link', k: 'section', n: labelOf(p), s: 'Page', page: p, anchor: '',
        hay: (labelOf(p) + ' page').toLowerCase() });
    });
    IX.views.forEach(function (v) {
      out.push({ t: 'link', k: 'section', n: v.n, s: labelOf(v.p), page: v.p, anchor: v.id,
        hay: (v.n + ' ' + labelOf(v.p)).toLowerCase() });
    });
    IX.cards.forEach(function (c) {
      out.push({ t: 'link', k: 'section', n: c.code + '  ' + c.n, s: labelOf(c.p), page: c.p,
        anchor: c.id, hay: (c.code + ' ' + c.n + ' ' + labelOf(c.p)).toLowerCase() });
    });
    universe = out;
  }

  function subseq(needle, hay) {
    var j = 0;
    for (var i = 0; i < hay.length && j < needle.length; i++) {
      if (hay.charCodeAt(i) === needle.charCodeAt(j)) j++;
    }
    return j === needle.length;
  }

  function score(item, q, tokens) {
    var name = item.n.toLowerCase();
    var s;
    if (name === q) s = 1000;
    else if (name.indexOf(q) === 0) s = 600;
    else if (new RegExp('(^|[\\s\\[_.\\-(])' + q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')).test(name)) s = 420;
    else if (name.indexOf(q) !== -1) s = 300;
    else {
      for (var i = 0; i < tokens.length; i++) {
        if (item.hay.indexOf(tokens[i]) === -1) {
          if (tokens.length === 1 && q.length > 2 && subseq(q, name)) return 20 + (KIND_WEIGHT[item.k] || 8) / 4;
          return -1;
        }
      }
      s = 120;
    }
    return s + (KIND_WEIGHT[item.k] || 8) - Math.min(40, name.length / 3);
  }

  function search(q) {
    q = q.trim().toLowerCase();
    var pool = universe.filter(function (it) {
      return kind === 'all' || it.k === kind;
    });
    if (!q) {
      if (kind !== 'all') {
        return pool.slice().sort(function (a, b) {
          return a.n.localeCompare(b.n);
        }).slice(0, 500);
      }
      var recent = (store('sma:recent') || []).map(function (id) {
        return universe.filter(function (it) {
          return it.id === id;
        })[0];
      }).filter(Boolean).slice(0, 6);
      recent.forEach(function (r) {
        r.recent = true;
      });
      var here = universe.filter(function (it) {
        return it.k === 'section' && it.page === PAGE && it.anchor && !/^card-/.test(it.anchor);
      });
      return recent.concat(here);
    }
    var tokens = q.split(/\s+/);
    var scored = [];
    pool.forEach(function (it) {
      var s = score(it, q, tokens);
      if (s > 0) scored.push([s, it]);
    });
    scored.sort(function (a, b) {
      return b[0] - a[0] || a[1].n.localeCompare(b[1].n);
    });
    return scored.slice(0, kind === 'all' ? 60 : 300).map(function (x) {
      return x[1];
    });
  }

  function counts(q) {
    var c = { all: 0 };
    q = q.trim().toLowerCase();
    var tokens = q.split(/\s+/);
    universe.forEach(function (it) {
      if (q && score(it, q, tokens) <= 0) return;
      c[it.k] = (c[it.k] || 0) + 1;
      c.all++;
    });
    return c;
  }

  function mark(text, q) {
    var frag = D.createDocumentFragment();
    var at = q ? text.toLowerCase().indexOf(q.trim().toLowerCase()) : -1;
    if (at < 0 || !q.trim()) {
      frag.appendChild(D.createTextNode(text));
      return frag;
    }
    var len = q.trim().length;
    frag.appendChild(D.createTextNode(text.slice(0, at)));
    frag.appendChild(el('mark', null, text.slice(at, at + len)));
    frag.appendChild(D.createTextNode(text.slice(at + len)));
    return frag;
  }

  function renderKinds(q) {
    var c = counts(q);
    kindsEl.textContent = '';
    ['all'].concat(KIND_ORDER).forEach(function (k) {
      if (k !== 'all' && !c[k] && kind !== k) return;
      var b = el('button', 'sma-kind' + (k === kind ? ' on' : ''));
      b.type = 'button';
      b.setAttribute('aria-pressed', k === kind ? 'true' : 'false');
      b.setAttribute('data-kind', k);
      b.appendChild(D.createTextNode(KIND_LABEL[k]));
      b.appendChild(el('span', 'dc-num', String(c[k] || 0)));
      b.tabIndex = -1;
      b.addEventListener('mousedown', function (e) {
        e.preventDefault();
      });
      b.addEventListener('click', function () {
        kind = k;
        update();
        input.focus();
      });
      kindsEl.appendChild(b);
    });
  }

  function render(q) {
    list.textContent = '';
    var lastGroup = null;
    var grouped = kind === 'all' && q.trim();
    results.forEach(function (it, i) {
      var group = !q.trim() ? (it.recent ? 'Recent' : 'On this page') : (grouped ? KIND_LABEL[it.k] : null);
      if (group && group !== lastGroup) {
        var h = el('li', 'sma-group', group);
        h.setAttribute('role', 'presentation');
        list.appendChild(h);
        lastGroup = group;
      }
      var li = el('li', 'sma-opt');
      li.id = 'sma-opt-' + i;
      li.setAttribute('role', 'option');
      li.setAttribute('aria-selected', i === active ? 'true' : 'false');
      var badge = el('span', 'sma-badge k-' + it.k.replace(/\s/g, '-'), it.k === 'calc group' ? 'calc' : it.k);
      if (it.lv) badge.className += ' lv-' + it.lv;
      if (it.st) badge.className += ' st-' + it.st;
      li.appendChild(badge);
      var main = el('span', 'sma-opt-main');
      var n = el('span', 'sma-opt-n');
      n.appendChild(mark(it.n, q));
      main.appendChild(n);
      if (it.s) main.appendChild(el('span', 'sma-opt-s', it.s));
      li.appendChild(main);
      var hint = it.t === 'link' && it.page !== PAGE ? labelOf(it.page) : (it.t === 'text' ? '' : '');
      if (it.lv) hint = it.lv;
      if (hint) li.appendChild(el('span', 'sma-opt-h', hint));
      li.addEventListener('mousedown', function (e) {
        e.preventDefault();
      });
      li.addEventListener('click', function () {
        choose(it);
      });
      li.addEventListener('mousemove', function () {
        if (active !== i) setActive(i, true);
      });
      list.appendChild(li);
    });
    if (!results.length) {
      var none = el('li', 'sma-none', q.trim() ? 'Nothing matches.' : 'Type to search.');
      none.setAttribute('role', 'presentation');
      list.appendChild(none);
    }
  }

  function setActive(i, noScroll) {
    var opts = $$('.sma-opt', list);
    if (active >= 0 && opts[active]) opts[active].setAttribute('aria-selected', 'false');
    active = i;
    if (opts[i]) {
      opts[i].setAttribute('aria-selected', 'true');
      input.setAttribute('aria-activedescendant', opts[i].id);
      if (!noScroll) opts[i].scrollIntoView({ block: 'nearest' });
    } else {
      input.removeAttribute('aria-activedescendant');
    }
  }

  function update() {
    var q = input.value;
    results = search(q);
    if (q.trim().length >= 2) {
      results.push({ t: 'text', k: 'section', n: 'Search the text of this page for “' + q.trim() + '”', s: 'Highlights every match, across all sections' });
    }
    active = results.length ? 0 : -1;
    renderKinds(q);
    render(q);
    setActive(active, true);
    list.scrollTop = 0;
    if (live) {
      var n = results.filter(function (r) {
        return r.t !== 'text';
      }).length;
      live.textContent = n + (n === 1 ? ' result' : ' results');
    }
  }

  function openPop() {
    if (!pop.hidden) return;
    pop.hidden = false;
    input.setAttribute('aria-expanded', 'true');
    update();
  }

  function closePop() {
    if (pop.hidden) return;
    pop.hidden = true;
    input.setAttribute('aria-expanded', 'false');
    input.removeAttribute('aria-activedescendant');
  }

  function openFinder(k) {
    if (!input) return;
    returnFocus = D.activeElement;
    kind = k && KIND_LABEL[k] ? k : 'all';
    if (k) input.value = '';
    input.focus();
    input.select();
    pop.hidden = true;
    openPop();
  }

  function remember(id) {
    var r = (store('sma:recent') || []).filter(function (x) {
      return x !== id;
    });
    r.unshift(id);
    store('sma:recent', r.slice(0, 8));
  }

  function choose(it) {
    if (!it) return;
    closePop();
    if (it.t === 'obj') {
      remember(it.id);
      input.blur();
      openObj(it.id);
    } else if (it.t === 'finding') {
      var f = IX.findings[it.fi];
      input.blur();
      if (f.o) openObj(f.o, it.fi);
      else go(f.p, f.p === 'pq' ? 'card-q17' : 'card-t1');
    } else if (it.t === 'link') {
      input.blur();
      if (it.anchor) go(it.page, it.anchor);
      else location.href = fileOf(it.page);
    } else if (it.t === 'text') {
      openFindbar(input.value.trim());
    }
  }

  function initFinder() {
    input = $('#sma-find');
    pop = $('#sma-pop');
    list = $('#sma-results');
    kindsEl = $('[data-sma-kinds]');
    live = $('[data-sma-live]');
    if (!input || !pop) return;
    buildUniverse();
    var timer = null;
    input.addEventListener('focus', openPop);
    input.addEventListener('input', function () {
      if (timer) clearTimeout(timer);
      timer = setTimeout(function () {
        if (pop.hidden) openPop();
        else update();
      }, 60);
    });
    input.addEventListener('keydown', function (e) {
      var opts = $$('.sma-opt', list);
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        if (pop.hidden) openPop();
        else setActive(Math.min(opts.length - 1, active + 1));
      } else if (e.key === 'ArrowUp') {
        e.preventDefault();
        setActive(Math.max(0, active - 1));
      } else if (e.key === 'PageDown' || e.key === 'PageUp') {
        e.preventDefault();
        setActive(Math.max(0, Math.min(opts.length - 1, active + (e.key === 'PageDown' ? 8 : -8))));
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (timer) {
          clearTimeout(timer);
          timer = null;
          update();
        }
        choose(results[active]);
      } else if (e.key === 'Tab' && !pop.hidden) {
        e.preventDefault();
        var ks = $$('.sma-kind', kindsEl).map(function (b) {
          return b.getAttribute('data-kind');
        });
        var at = ks.indexOf(kind);
        kind = ks[(at + (e.shiftKey ? -1 : 1) + ks.length) % ks.length];
        update();
      } else if (e.key === 'Escape') {
        e.preventDefault();
        if (!pop.hidden) closePop();
        else input.value = '';
        input.blur();
        if (returnFocus && returnFocus.focus && returnFocus !== D.body) returnFocus.focus();
      }
    });
    input.addEventListener('blur', function () {
      setTimeout(function () {
        if (D.activeElement !== input && !pop.contains(D.activeElement)) closePop();
      }, 120);
    });
    D.addEventListener('mousedown', function (e) {
      if (!pop.hidden && !pop.contains(e.target) && e.target !== input) closePop();
    });
  }

  /* ============================================================ Inspector ===== */

  var drawer, dBody, dKind, dTitle, dSub, dBack;
  var stack = [];
  var openId = null;
  var drawerReturn = null;

  function chipFor(id, badge) {
    var o = BY[id];
    var b = el('button', 'sma-ref');
    b.type = 'button';
    if (badge) b.appendChild(el('span', 'sma-badge k-' + o.k.replace(/\s/g, '-'), o.k === 'calc group' ? 'calc' : o.k));
    b.appendChild(el('span', 'sma-ref-n', o.n));
    b.addEventListener('click', function () {
      openObj(id);
    });
    return b;
  }

  function section(title, count) {
    var s = el('section', 'sma-d-sec');
    var h = el('p', 'sma-d-h', title);
    if (count != null) h.appendChild(el('span', 'dc-num', String(count)));
    s.appendChild(h);
    return s;
  }

  function copyButton(text) {
    var b = el('button', 'sma-copy', 'Copy');
    b.type = 'button';
    b.addEventListener('click', function () {
      var done = function () {
        b.textContent = 'Copied';
        setTimeout(function () {
          b.textContent = 'Copy';
        }, 1200);
      };
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(text).then(done, function () {});
      }
    });
    return b;
  }

  function openObj(id, findingIndex, fromBack) {
    var o = BY[id];
    if (!o || !drawer) return;
    if (openId && openId !== id && !fromBack) stack.push(openId);
    if (!openId) drawerReturn = D.activeElement;
    openId = id;
    remember(id);
    dBack.hidden = !stack.length;
    dKind.textContent = KIND_LABEL[o.k] ? KIND_LABEL[o.k].replace(/s$/, '').replace(/ie$/, 'y') : o.k;
    dKind.className = 'sma-d-kind sma-badge k-' + o.k.replace(/\s/g, '-');
    dTitle.textContent = o.n;
    dSub.textContent = o.full && o.full !== o.n ? o.full : (o.s || '');
    dBody.textContent = '';

    if (o.d) dBody.appendChild(el('p', 'sma-d-desc', o.d));

    var fi = o.fi || [];
    if (fi.length) {
      var fs = section('Findings', fi.length);
      var ul = el('ul', 'sma-d-finds');
      fi.forEach(function (i) {
        var f = IX.findings[i];
        var li = el('li', 'lv-' + f.lv + (i === findingIndex ? ' is-picked' : ''));
        var head = el('p', 'sma-d-fhead');
        head.appendChild(el('span', 'dcv-chip ' + ({ error: 'bad', warning: 'warn', info: 'note' }[f.lv] || ''), f.lv));
        head.appendChild(el('code', null, f.code));
        li.appendChild(head);
        li.appendChild(el('p', 'sma-d-fmsg', f.msg.replace(/`/g, '')));
        var why = (IX.help || {})[f.code];
        if (why) {
          var det = el('details', 'sma-how');
          det.appendChild(el('summary', null, 'Why it matters'));
          det.appendChild(el('p', 'sma-how-body', why));
          li.appendChild(det);
        }
        ul.appendChild(li);
      });
      fs.appendChild(ul);
      dBody.appendChild(fs);
    }

    if (o.f && o.f.length) {
      var facts = section('Facts');
      var dl = el('dl', 'sma-d-facts');
      o.f.forEach(function (kv) {
        dl.appendChild(el('dt', null, kv[0]));
        dl.appendChild(el('dd', null, kv[1]));
      });
      facts.appendChild(dl);
      dBody.appendChild(facts);
    }

    if (o.code) {
      var cs = section(o.lang === 'm' ? 'Power Query M' : 'DAX');
      cs.querySelector('.sma-d-h').appendChild(copyButton(o.code));
      cs.appendChild(el('pre', 'dcv-code sma-d-code', o.code));
      dBody.appendChild(cs);
    }

    (o.l || []).forEach(function (grp) {
      var ls = section(grp[0], grp[1].length);
      var wrap = el('div', 'sma-refs');
      var mixed = grp[1].some(function (rid) {
        return BY[rid] && BY[rid].k !== BY[grp[1][0]].k;
      });
      grp[1].forEach(function (rid) {
        if (BY[rid]) wrap.appendChild(chipFor(rid, mixed));
      });
      ls.appendChild(wrap);
      dBody.appendChild(ls);
    });

    var foot = el('div', 'sma-d-foot');
    if (o.w) foot.appendChild(el('p', 'sma-d-where', 'Declared at ' + o.w));
    var acts = el('div', 'sma-d-acts');
    ((IX.go || {})[o.k] || []).forEach(function (g) {
      var b = el('button', 'dc-btn secondary', g[0]);
      b.type = 'button';
      if (g[1] !== PAGE) b.appendChild(el('span', 'sma-d-page', labelOf(g[1])));
      b.addEventListener('click', function () {
        var anchor = g[2] + (g[3] ? '?pick=' + encodeURIComponent(o.n) : '');
        closeDrawer();
        go(g[1], anchor);
      });
      acts.appendChild(b);
    });
    foot.appendChild(acts);
    dBody.appendChild(foot);

    drawer.hidden = false;
    D.body.setAttribute('data-sma-drawer', 'open');
    dBody.scrollTop = 0;
    drawer.focus();
  }

  function closeDrawer() {
    if (!drawer || drawer.hidden) return;
    drawer.hidden = true;
    D.body.removeAttribute('data-sma-drawer');
    stack = [];
    openId = null;
    if (drawerReturn && drawerReturn !== input && drawerReturn.focus && D.contains(drawerReturn)) {
      drawerReturn.focus();
    }
    drawerReturn = null;
  }

  function initDrawer() {
    drawer = $('#sma-drawer');
    if (!drawer) return;
    drawer.tabIndex = -1;
    dBody = $('[data-sma-d-body]', drawer);
    dKind = $('[data-sma-d-kind]', drawer);
    dTitle = $('[data-sma-d-title]', drawer);
    dSub = $('[data-sma-d-sub]', drawer);
    dBack = $('[data-sma-back]', drawer);
    $('[data-sma-close]', drawer).addEventListener('click', closeDrawer);
    dBack.addEventListener('click', function () {
      var prev = stack.pop();
      if (prev) openObj(prev, null, true);
    });
  }

  /* ======================================================= Find in text ===== */

  var findbar = null;
  var viewBeforeFind = null;

  function openFindbar(q) {
    findbar = findbar || $('[data-sma-findbar]');
    var box = $('#dc-find');
    if (!findbar || !box) return;
    if (views.length && !allMode) {
      viewBeforeFind = current;
      showAll();
    }
    findbar.hidden = false;
    closePop();
    if (q != null) {
      box.value = q;
      box.dispatchEvent(new Event('input'));
      /* Enter in the theme's search runs it at once and jumps to the first hit. */
      box.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
    }
    box.focus();
    box.select();
  }

  function closeFindbar() {
    var box = $('#dc-find');
    if (!findbar || findbar.hidden) return;
    if (box) {
      box.value = '';
      box.dispatchEvent(new Event('input'));
    }
    findbar.hidden = true;
    if (viewBeforeFind) show(viewBeforeFind, true);
    viewBeforeFind = null;
  }

  function initFindbar() {
    findbar = $('[data-sma-findbar]');
    if (!findbar) return;
    $('[data-sma-findbar-close]', findbar).addEventListener('click', closeFindbar);
    $('#dc-find').addEventListener('keydown', function (e) {
      if (e.key === 'Escape') setTimeout(closeFindbar, 0);
    });
  }

  /* ========================================================= AI filter ===== */

  function initAiFilter() {
    var chips = $$('[data-ai-filter]');
    if (!chips.length) return;
    chips.forEach(function (c) {
      c.addEventListener('click', function () {
        var f = c.getAttribute('data-ai-filter');
        chips.forEach(function (x) {
          var on = x === c;
          x.classList.toggle('on', on);
          x.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        if (f === 'all') D.body.removeAttribute('data-ai-filter');
        else D.body.setAttribute('data-ai-filter', f);
        $$('[data-ai-group]').forEach(function (g) {
          var any = f === 'all' || $$('.dcv-check[data-status="' + f + '"]', g).length > 0;
          g.classList.toggle('is-empty', !any);
        });
      });
    });
  }

  /* ============================================================= Help ===== */

  var help = null;
  var helpReturn = null;

  function openHelp() {
    help = help || $('[data-sma-helpdlg]');
    if (!help) return;
    helpReturn = D.activeElement;
    help.hidden = false;
    $('[data-sma-help-close]', help).focus();
  }

  function closeHelp() {
    if (!help || help.hidden) return false;
    help.hidden = true;
    if (helpReturn && helpReturn.focus) helpReturn.focus();
    return true;
  }

  function initHelp() {
    help = $('[data-sma-helpdlg]');
    var btn = $('[data-sma-help]');
    if (btn) btn.addEventListener('click', openHelp);
    if (!help) return;
    $('[data-sma-help-close]', help).addEventListener('click', closeHelp);
    help.addEventListener('click', function (e) {
      if (e.target === help) closeHelp();
    });
    help.addEventListener('keydown', function (e) {
      if (e.key === 'Tab') {
        e.preventDefault();
        $('[data-sma-help-close]', help).focus();
      }
    });
  }

  /* =========================================================== Lineage ===== */

  /* Source to measure, left to right. A node's column is its longest path from a
     source, so a deep chain reads as depth; measure hosts take the last column. Rows
     are ordered by the average row of each node's parents, which keeps most edges flat. */
  var NODE_W = 196;
  var NODE_H = 50;
  var COL_W = 252;
  var ROW_H = 64;
  var PAD = 20;

  function initLineage() {
    $$('[data-sma-lineage]').forEach(drawLineage);
  }

  function drawLineage(mount) {
    var key = mount.getAttribute('data-sma-lineage');
    var island = D.getElementById(key);
    if (!island) return;
    var g;
    try {
      g = JSON.parse(island.textContent);
    } catch (e) {
      return;
    }
    if (!g.nodes || g.nodes.length < 2) return;
    var byId = {};
    var out = {};
    var inc = {};
    g.nodes.forEach(function (n) {
      byId[n.id] = n;
      out[n.id] = [];
      inc[n.id] = [];
    });
    g.edges.forEach(function (e) {
      if (!byId[e[0]] || !byId[e[1]]) return;
      out[e[0]].push(e[1]);
      inc[e[1]].push(e[0]);
    });

    var layer = {};
    function depth(id, seen) {
      if (layer[id] != null) return layer[id];
      if (seen[id]) return 0;
      seen[id] = true;
      var d = 0;
      inc[id].forEach(function (p) {
        d = Math.max(d, depth(p, seen) + 1);
      });
      layer[id] = d;
      return d;
    }
    g.nodes.forEach(function (n) {
      depth(n.id, {});
    });
    var last = 0;
    g.nodes.forEach(function (n) {
      if (n.kind !== 'measures') last = Math.max(last, layer[n.id]);
    });
    g.nodes.forEach(function (n) {
      if (n.kind === 'measures') layer[n.id] = last + 1;
    });

    var cols = [];
    g.nodes.forEach(function (n) {
      (cols[layer[n.id]] = cols[layer[n.id]] || []).push(n.id);
    });
    cols = cols.filter(Boolean);
    var row = {};
    cols.forEach(function (c) {
      c.forEach(function (id, i) {
        row[id] = i;
      });
    });
    for (var sweep = 0; sweep < 3; sweep++) {
      cols.forEach(function (c, ci) {
        if (!ci) return;
        c.sort(function (a, b) {
          var avg = function (id) {
            var ps = inc[id];
            if (!ps.length) return row[id];
            return ps.reduce(function (s, p) {
              return s + row[p];
            }, 0) / ps.length;
          };
          return avg(a) - avg(b);
        });
        c.forEach(function (id, i) {
          row[id] = i;
        });
      });
    }
    var tallest = Math.max.apply(null, cols.map(function (c) {
      return c.length;
    }));
    var pos = {};
    cols.forEach(function (c, ci) {
      var offset = (tallest - c.length) * ROW_H / 2;
      c.forEach(function (id, i) {
        pos[id] = { x: PAD + ci * COL_W, y: PAD + offset + i * ROW_H };
      });
    });
    var W = PAD * 2 + (cols.length - 1) * COL_W + NODE_W;
    var H = PAD * 2 + (tallest - 1) * ROW_H + NODE_H;

    mount.textContent = '';
    mount.style.width = W + 'px';
    mount.style.height = H + 'px';
    var NS = 'http://www.w3.org/2000/svg';
    var svg = D.createElementNS(NS, 'svg');
    svg.setAttribute('class', 'sma-lin-edges');
    svg.setAttribute('width', W);
    svg.setAttribute('height', H);
    svg.setAttribute('aria-hidden', 'true');
    mount.appendChild(svg);

    var paths = [];
    g.edges.forEach(function (e) {
      var a = pos[e[0]];
      var b = pos[e[1]];
      if (!a || !b) return;
      var x1 = a.x + NODE_W;
      var y1 = a.y + NODE_H / 2;
      var x2 = b.x;
      var y2 = b.y + NODE_H / 2;
      var mx = (x1 + x2) / 2;
      var p = D.createElementNS(NS, 'path');
      p.setAttribute('d', 'M' + x1 + ',' + y1 + ' C' + mx + ',' + y1 + ' ' + mx + ',' + y2 + ' ' + x2 + ',' + y2);
      p.setAttribute('class', 'sma-le');
      svg.appendChild(p);
      paths.push({ a: e[0], b: e[1], el: p });
    });

    var els = {};
    var pinned = null;
    g.nodes.forEach(function (n) {
      var d = el('div', 'sma-ln k-' + n.kind + ' tone-' + n.tone + (n.hidden ? ' is-hidden' : '') +
        (n.flag ? ' is-flagged' : ''));
      d.style.left = pos[n.id].x + 'px';
      d.style.top = pos[n.id].y + 'px';
      d.tabIndex = 0;
      d.setAttribute('role', 'button');
      d.setAttribute('aria-label', n.label + ', ' + n.meta + (n.flag ? ', ' + n.flag : ''));
      if (n.flag) d.title = n.flag;
      d.appendChild(el('span', 'sma-ln-n', n.label));
      d.appendChild(el('span', 'sma-ln-m', n.meta));
      if (n.flag) {
        var f = el('span', 'sma-ln-flag', '!');
        f.setAttribute('aria-hidden', 'true');
        d.appendChild(f);
      }
      d.addEventListener('mouseenter', function () {
        if (!pinned) trace(n.id);
      });
      d.addEventListener('mouseleave', function () {
        if (!pinned) clear();
      });
      d.addEventListener('focus', function () {
        if (!pinned) trace(n.id);
      });
      d.addEventListener('blur', function () {
        if (!pinned) clear();
      });
      d.addEventListener('click', function (e) {
        e.stopPropagation();
        pinned = pinned === n.id ? null : n.id;
        if (pinned) trace(n.id);
        else clear();
      });
      d.addEventListener('dblclick', function () {
        if (n.obj && BY[n.obj]) openObj(n.obj);
      });
      d.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && n.obj && BY[n.obj]) {
          e.preventDefault();
          openObj(n.obj);
        } else if (e.key === ' ') {
          e.preventDefault();
          d.click();
        }
      });
      mount.appendChild(d);
      els[n.id] = d;
    });

    function reach(id) {
      var keep = {};
      keep[id] = true;
      (function up(x) {
        inc[x].forEach(function (p) {
          if (!keep[p]) {
            keep[p] = true;
            up(p);
          }
        });
      })(id);
      var down = {};
      (function dn(x) {
        out[x].forEach(function (c) {
          if (!down[c]) {
            down[c] = true;
            keep[c] = true;
            dn(c);
          }
        });
      })(id);
      return keep;
    }
    function trace(id) {
      var keep = reach(id);
      mount.classList.add('is-tracing');
      Object.keys(els).forEach(function (k) {
        els[k].classList.toggle('is-on', !!keep[k]);
        els[k].classList.toggle('is-focus', k === id);
      });
      paths.forEach(function (p) {
        p.el.classList.toggle('is-on', !!(keep[p.a] && keep[p.b]));
      });
    }
    function clear() {
      mount.classList.remove('is-tracing');
      Object.keys(els).forEach(function (k) {
        els[k].classList.remove('is-on', 'is-focus');
      });
      paths.forEach(function (p) {
        p.el.classList.remove('is-on');
      });
    }
    mount.addEventListener('click', function () {
      pinned = null;
      clear();
    });
    var reset = $('[data-sma-lin-reset="' + key.replace(/-data$/, '') + '"]');
    if (reset) {
      reset.addEventListener('click', function () {
        pinned = null;
        clear();
      });
    }
  }

  /* ============================================================== Keys ===== */

  /* Capture phase on the window, so these win over the theme's own '/' and Ctrl+K
     (which focus its in-page search) without editing the theme. */
  function initKeys() {
    W.addEventListener('keydown', function (e) {
      var k = e.key;
      var mod = e.ctrlKey || e.metaKey;
      var inField = typing(e.target);
      var claim = function () {
        e.preventDefault();
        e.stopImmediatePropagation();
      };
      if (mod && !e.altKey && (k === 'k' || k === 'K')) {
        claim();
        openFinder();
        return;
      }
      if (e.altKey && !mod && (k === 'f' || k === 'F' || e.code === 'KeyF')) {
        claim();
        openFindbar(null);
        return;
      }
      if (k === 'Escape') {
        if (closeHelp()) return claim();
        if (drawer && !drawer.hidden && !(input && D.activeElement === input)) {
          claim();
          closeDrawer();
        }
        return;
      }
      if (inField || mod || e.altKey) return;
      if (k === '/') {
        claim();
        openFinder();
      } else if (k === '?') {
        claim();
        openHelp();
      } else if (k === '[' && views.length) {
        claim();
        step(-1);
      } else if (k === ']' && views.length) {
        claim();
        step(1);
      } else if ((k === 'a' || k === 'A') && views.length) {
        claim();
        if (allMode) show(current || views[0].getAttribute('data-view'), true);
        else showAll();
      } else if (/^[1-9]$/.test(k) && tabs.length) {
        var t = tabs[+k - 1];
        if (t) {
          claim();
          navigate(t.getAttribute('data-view'));
        }
      }
    }, true);
  }

  function boot() {
    load();
    initViews();
    initBrowse();
    initFinder();
    initDrawer();
    initFindbar();
    initAiFilter();
    initHelp();
    initLineage();
    initKeys();
    D.body.classList.add('sma-app');
    route();
  }

  if (D.readyState === 'loading') D.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
