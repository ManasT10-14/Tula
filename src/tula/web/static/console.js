/* Session-bound CSRF and useful form failures shared by all officer pages. */
window.tulaCSRF = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
document.addEventListener('htmx:configRequest', event => {event.detail.headers['X-CSRF-Token'] = window.tulaCSRF();});
(() => {
  // Every review form on this page writes the same inspection revision.
  let pendingReview = false;
  const recovery = 'Your entries are still here. Open the latest record in a new tab to check whether the change was saved before retrying.';

  function feedback(form) {
    let box = form.querySelector('[data-review-feedback]');
    if (!box) {
      box = document.createElement('div');
      box.setAttribute('data-review-feedback', '');
      form.append(box);
    }
    box.replaceChildren();
    return box;
  }

  function failure(box, message, record, signIn = false) {
    box.className = 'error-box';
    box.setAttribute('role', 'alert');
    const text = document.createElement('p');
    text.textContent = message;
    const link = document.createElement('a');
    link.href = signIn ? `/login?next=${encodeURIComponent(record)}` : record;
    link.target = '_blank';
    link.rel = 'noopener';
    link.textContent = signIn ? 'Sign in and check the record in a new tab' : 'Open latest record in a new tab';
    box.replaceChildren(text, link);
  }

  function detailMessage(result) {
    if (typeof result?.detail === 'string') return result.detail;
    if (!Array.isArray(result?.detail)) return '';
    return result.detail.slice(0, 5).map(error => {
      if (typeof error?.msg !== 'string') return '';
      const field = Array.isArray(error.loc) ? error.loc.at(-1) : '';
      return `${typeof field === 'string' ? field.replaceAll('_', ' ') + ': ' : ''}${error.msg}`;
    }).filter(Boolean).join(' ');
  }

  document.addEventListener('submit', async event => {
    const form = event.target;
    if (!form.matches('[data-review-form]')) return;
    event.preventDefault();
    if (pendingReview) return;

    const action = new URL(form.getAttribute('action'), location.href);
    const record = action.pathname.split('/').slice(0, 3).join('/');
    const data = new FormData(form);
    if (event.submitter?.name) data.set(event.submitter.name, event.submitter.value);
    const forms = [...document.querySelectorAll('[data-review-form]')];
    const controls = forms.flatMap(item => [...item.querySelectorAll('input, select, textarea, button')])
      .map(control => [control, control.disabled]);
    const box = feedback(form);
    box.className = 'notice small';
    box.setAttribute('role', 'status');
    box.textContent = 'Saving review…';
    pendingReview = true;
    controls.forEach(([control]) => { control.disabled = true; });
    forms.forEach(item => item.setAttribute('data-review-pending', 'true'));
    form.setAttribute('aria-busy', 'true');
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 45000);
    try {
      const response = await fetch(action.href, {
        method: 'POST', headers: {'X-CSRF-Token': window.tulaCSRF()},
        body: data, signal: controller.signal
      });
      if (controller.signal.aborted) throw new Error('timeout');
      const destination = response.redirected ? new URL(response.url, location.href) : null;
      if (response.status === 401 || (destination?.origin === location.origin && destination.pathname === '/login')) {
        failure(box, 'Your session has expired. Your entries are still here. Sign in and check the latest record in a new tab, then copy any unsaved entries into that record and save there. This tab has the previous session token.', record, true);
        return;
      }
      if (response.ok && destination?.origin === location.origin && destination.pathname === record) {
        // Assigning the same path with a fragment can leave stale content/revisions.
        // Fetch can omit the fragment from Response.url; retain the endpoint's section.
        const sections = {'/review': '#review', '/context': '#package-context',
          '/allergens': '#allergen-analysis', '/intelligence/correct': '#label-intelligence',
          '/import': '#imported', '/activate': '#active-version'};
        const section = destination.hash || sections[action.pathname.slice(record.length)] || '';
        window.history.replaceState(null, '', destination.pathname + destination.search + section);
        location.reload();
        return;
      }
      if (response.status >= 500) {
        failure(box, `The server could not confirm this change. ${recovery}`, record);
        return;
      }
      let result = null;
      try { result = await response.json(); } catch (_) { /* HTML and empty errors are valid failures too. */ }
      if (controller.signal.aborted) throw new Error('timeout');
      const detail = detailMessage(result);
      let message;
      if (response.status === 409) message = detail || 'The record changed or its evidence needs attention.';
      else if (response.status === 403) message = (detail || 'This account or session cannot save the change.') + ' If you signed in again, copy unsaved entries into the latest record and save there.';
      else if (response.status === 429) message = 'Too many requests. Wait before trying again.';
      else if (!response.ok) message = detail || 'Check the required fields and the latest record before trying again.';
      else message = 'The server returned an unexpected response; the change was not confirmed.';
      failure(box, `${message} ${recovery}`, record);
    } catch (_) {
      const message = controller.signal.aborted ? 'The save confirmation timed out.' : 'The connection was interrupted; the change was not confirmed.';
      failure(box, `${message} ${recovery}`, record);
    } finally {
      clearTimeout(timer);
      controls.forEach(([control, disabled]) => { control.disabled = disabled; });
      forms.forEach(item => item.removeAttribute('data-review-pending'));
      form.removeAttribute('aria-busy');
      pendingReview = false;
    }
  });
})();

/* Install the console as an app on a handset.
   navigator.serviceWorker is undefined outside a secure context, so this is a
   no-op over LAN HTTP -- which never reaches a page anyway, since the transport
   policy answers 426 there. Registration failure is not worth troubling an
   officer with: the console works identically without it. */
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js', { scope: '/' }).catch(() => {});
  });
}
