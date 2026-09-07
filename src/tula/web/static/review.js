(() => {
  const form = document.getElementById('correction-form');
  if (!form) return;
  const $ = id => document.getElementById(id);
  const data = JSON.parse($('declaration-data').textContent);
  const canvas = $('correction-canvas'), context = canvas.getContext('2d');
  const coordinates = [0, 1, 2, 3].map(i => $('bbox-' + i));
  const frame = $('correction-frame'), status = $('correction-source-status'), failure = $('correction-geometry-error');
  let image = null, selected = null, drag = null, generation = 0, deferred = null, requestedBox = null;
  const pending = () => form.getAttribute('data-review-pending') === 'true';

  function draw() {
    context.clearRect(0, 0, canvas.width, canvas.height);
    if (!image) return;
    context.drawImage(image, 0, 0);
    if (!selected) return;
    context.strokeStyle = '#bc6227'; context.lineWidth = Math.max(3, canvas.width / 250);
    context.strokeRect(selected[0], selected[1], selected[2] - selected[0], selected[3] - selected[1]);
    context.fillStyle = '#bc822233';
    context.fillRect(selected[0], selected[1], selected[2] - selected[0], selected[3] - selected[1]);
  }

  function validate() {
    if (pending()) return false;
    const bounds = coordinates.map(input => input.value.trim() === '' ? NaN : Number(input.value));
    const [left, top, right, bottom] = bounds;
    const valid = !!image && bounds.every(Number.isInteger)
      && left >= 0 && top >= 0 && right > left && bottom > top
      && right <= image.naturalWidth && bottom <= image.naturalHeight;
    const message = image ? 'Enter four whole-pixel bounds inside the loaded image, with Right greater than Left and Bottom greater than Top.'
      : 'Load the source image successfully before selecting evidence.';
    coordinates.forEach(input => { input.setCustomValidity(valid ? '' : message); input.setAttribute('aria-invalid', String(!valid)); });
    frame.setCustomValidity(image ? '' : message);
    failure.textContent = image && !valid ? message : '';
    selected = valid ? bounds : null;
    $('correction-bbox').value = JSON.stringify(selected || []);
    draw();
    return valid;
  }

  function writeBounds(bounds) {
    coordinates.forEach((input, index) => { input.value = bounds?.[index] ?? ''; });
    validate();
  }

  function releaseDrag() {
    const previous = drag; drag = null;
    if (previous && canvas.hasPointerCapture?.(previous.id)) canvas.releasePointerCapture(previous.id);
    return previous;
  }

  function finishLoad(result) {
    if (result.generation !== generation || result.frame !== frame.value) return;
    if (pending()) { deferred = result; return; }
    deferred = null;
    if (result.error || !result.image.naturalWidth || !result.image.naturalHeight) {
      image = null; selected = null;
      status.textContent = 'Source image could not be loaded. Retry the image; if your session expired, sign in and reopen this record.';
      $('correction-retry-image').hidden = false;
      validate();
      return;
    }
    image = result.image; canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
    coordinates.forEach((input, index) => { input.max = index % 2 ? image.naturalHeight : image.naturalWidth; });
    status.textContent = `Source image loaded: ${image.naturalWidth} × ${image.naturalHeight} pixels. Select the complete printed declaration.`;
    $('correction-retry-image').hidden = true;
    writeBounds(requestedBox);
  }

  function load(box = null) {
    if (pending()) return;
    releaseDrag(); image = null; selected = null; deferred = null;
    requestedBox = Array.isArray(box) ? [...box] : null;
    const loadGeneration = ++generation, selectedFrame = frame.value;
    // Clear both old pixels and old submitted geometry before the next request.
    context.clearRect(0, 0, canvas.width, canvas.height); canvas.width = 1; canvas.height = 1;
    writeBounds(null);
    coordinates.forEach(input => input.removeAttribute('max'));
    $('correction-retry-image').hidden = true;
    status.textContent = 'Loading source image… Evidence selection is unavailable until it loads.';
    if (selectedFrame === '' || !/^\d+$/.test(selectedFrame)) {
      status.textContent = 'No source image is available for this correction.';
      return;
    }
    const next = new Image();
    next.onload = () => finishLoad({generation: loadGeneration, frame: selectedFrame, image: next});
    next.onerror = () => finishLoad({generation: loadGeneration, frame: selectedFrame, image: next, error: true});
    const base = form.getAttribute('action').replace(/\/review$/, '');
    next.src = `${base}/frames/${selectedFrame}`;
  }

  function declaration() {
    if (pending()) return;
    const kind = $('correction-kind'), declaration = data[kind.value];
    const sourceFrame = kind.selectedOptions[0]?.dataset.frame;
    if (sourceFrame !== undefined && Array.from(frame.options).some(option => option.value === sourceFrame)) frame.value = sourceFrame;
    $('correction-raw').value = declaration?.raw || '';
    load(declaration?.bbox || null);
  }
  $('correction-kind').onchange = declaration;
  frame.onchange = () => { if (!pending()) load(); };
  $('correction-retry-image').onclick = () => { if (!pending()) load(requestedBox); };
  coordinates.forEach(input => { input.oninput = () => { if (!pending()) { releaseDrag(); validate(); } }; });

  function point(event) {
    const rect = canvas.getBoundingClientRect();
    if (!image || !rect.width || !rect.height || !Number.isFinite(event.clientX) || !Number.isFinite(event.clientY)) return null;
    return [Math.round(Math.max(0, Math.min(canvas.width, (event.clientX - rect.left) * canvas.width / rect.width))),
      Math.round(Math.max(0, Math.min(canvas.height, (event.clientY - rect.top) * canvas.height / rect.height)))];
  }

  function move(event) {
    if (pending()) { releaseDrag(); return; }
    if (!drag || drag.id !== event.pointerId) return;
    const end = point(event);
    if (!end) return;
    const start = drag.start;
    writeBounds([Math.min(start[0], end[0]), Math.min(start[1], end[1]), Math.max(start[0], end[0]), Math.max(start[1], end[1])]);
  }

  canvas.onpointerdown = event => {
    if (pending() || drag || event.isPrimary === false || event.button !== 0) return;
    const start = point(event);
    if (!start) return;
    event.preventDefault();
    drag = {id: event.pointerId, start, previous: coordinates.map(input => input.value)};
    canvas.setPointerCapture(event.pointerId);
    writeBounds([start[0], start[1], start[0], start[1]]);
  };
  canvas.onpointermove = move;
  canvas.onpointerup = event => {
    if (!drag || drag.id !== event.pointerId) return;
    move(event); releaseDrag();
  };
  function cancel(event) {
    if (!drag || drag.id !== event.pointerId) return;
    const previous = releaseDrag();
    if (!pending()) writeBounds(previous.previous);
  }
  canvas.onpointercancel = cancel;
  canvas.onlostpointercapture = cancel;

  // The shared submit handler disables controls and owns restoring their state.
  // Custom validity prevents invalid geometry without fighting that restoration.
  form.addEventListener('submit', event => {
    if (pending() || !validate()) {
      event.preventDefault(); event.stopPropagation();
      if (!pending()) coordinates[0].reportValidity();
    }
  }, true);
  new MutationObserver(() => {
    if (pending()) { releaseDrag(); return; }
    if (deferred) finishLoad(deferred);
  }).observe(form, {attributes: true, attributeFilter: ['data-review-pending']});
  declaration();
})();
