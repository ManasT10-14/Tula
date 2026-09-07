/* Record tabs.

   An inspection record is long by necessity: findings, declarations, evidence
   images, label intelligence and the audit trail all have to be there. Making
   an officer scroll past four of them to reach the fifth is what made the page
   hard to work with, so the sections become panels.

   Progressive by construction. Without this script every panel stays in the
   document and the tab strip is a set of in-page anchors, so the page is never
   less usable than it was — and printing, Ctrl+F and screen readers that
   linearise the document keep working. */
(() => {
  const strips = document.querySelectorAll('[data-tabs]');
  if (!strips.length) return;

  strips.forEach(strip => {
    const tabs = [...strip.querySelectorAll('[role=tab]')];
    const panels = tabs
      .map(tab => document.getElementById(tab.getAttribute('aria-controls')))
      .filter(Boolean);
    if (tabs.length !== panels.length || !panels.length) return;

    const storeKey = 'tula.tab.' + strip.dataset.tabs + '.' + location.pathname;

    function show(index, {focus = false, remember = true} = {}) {
      tabs.forEach((tab, i) => {
        const selected = i === index;
        tab.setAttribute('aria-selected', selected ? 'true' : 'false');
        tab.tabIndex = selected ? 0 : -1;
        panels[i].hidden = !selected;
      });
      if (focus) tabs[index].focus();
      if (!remember) return;
      try { sessionStorage.setItem(storeKey, tabs[index].getAttribute('aria-controls')); } catch (_) {}
    }

    // A deep link wins over a remembered tab: console.js reloads the record with
    // #review (and friends) after a save, and that section must be on screen.
    function fromHash() {
      const id = decodeURIComponent(location.hash.slice(1));
      if (!id) return -1;
      const target = document.getElementById(id);
      if (!target) return -1;
      const panel = target.closest('[role=tabpanel]');
      return panel ? panels.indexOf(panel) : -1;
    }

    function fromStore() {
      let saved = null;
      try { saved = sessionStorage.getItem(storeKey); } catch (_) {}
      return saved ? tabs.findIndex(tab => tab.getAttribute('aria-controls') === saved) : -1;
    }

    tabs.forEach((tab, index) => {
      tab.addEventListener('click', event => {
        event.preventDefault();
        show(index);
        // Keep the address bar honest without adding a history entry per tab.
        history.replaceState(null, '', location.pathname + location.search + '#' + tab.getAttribute('aria-controls'));
        panels[index].scrollIntoView({block: 'nearest'});
      });
      tab.addEventListener('keydown', event => {
        const moves = {ArrowRight: 1, ArrowLeft: -1, Home: 'first', End: 'last'};
        const move = moves[event.key];
        if (move === undefined) return;
        event.preventDefault();
        const next = move === 'first' ? 0
          : move === 'last' ? tabs.length - 1
          : (index + move + tabs.length) % tabs.length;
        show(next, {focus: true});
      });
    });

    const opening = [fromHash(), fromStore()].find(index => index >= 0);
    show(opening === undefined ? 0 : opening, {remember: false});

    window.addEventListener('hashchange', () => {
      const index = fromHash();
      if (index >= 0) show(index);
    });
  });
})();
