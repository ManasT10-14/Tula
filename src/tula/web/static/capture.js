(() => {
  const $ = id => document.getElementById(id);
  const initial = JSON.parse($('capture-initial-data')?.textContent || '{}');
  let priorCount = Number.isInteger(initial.prior_count) ? initial.prior_count : 0;
  const items = [];
  const draftsEnabled = $('capture-drafts')?.dataset?.enabled === 'true';
  let activeDraft = null, draftSaving = false, draftLoading = false, draftGeneration = 0, savedGeneration = -1, saveAttempt = null;
  const draftMessage = message => { if (draftsEnabled) $('draft-status').textContent = message; };
  const markDirty = () => {
    if (!draftsEnabled || draftLoading) return;
    draftGeneration++;
    if (activeDraft) draftMessage('You have unsaved capture changes. Use Save capture draft to keep them.');
  };
  let editor = null, selection = null, pointer = null, camera = null, pollTimer = null;
  let capturedGeo = null;
  let fileQueue = Promise.resolve(), qualityQueue = Promise.resolve(), uploading = false, runningJob = false;
  const panelOptions = [['pdp','Front / principal display'],['back','Back'],['left','Left'],['right','Right'],['top','Top'],['bottom','Bottom'],['unknown','Close-up / other']];
  const error = message => { $('capture-error').textContent = message; };
  const selectValue = (id, value) => {
    if (Array.from($(id).options).some(option => option.value === value)) $(id).value = value;
  };
  function showLocation(message = '') {
    $('geo').value = capturedGeo ? JSON.stringify(capturedGeo) : '';
    $('clear-location').hidden = !capturedGeo;
    $('location-status').textContent = message || (capturedGeo
      ? `Recorded latitude ${capturedGeo[0].toFixed(5)}, longitude ${capturedGeo[1].toFixed(5)}.`
      : 'No coordinates recorded. Location access is requested only after you select the button.');
  }
  $('record-location').onclick = () => {
    if (!navigator.geolocation) {
      showLocation('Device location is unavailable here. Enter the district and continue without coordinates.');
      return;
    }
    $('record-location').disabled = true;
    showLocation('Requesting one location reading from this device…');
    navigator.geolocation.getCurrentPosition(position => {
      const latitude = Number(position.coords.latitude), longitude = Number(position.coords.longitude);
      if (!Number.isFinite(latitude) || !Number.isFinite(longitude) || latitude < -90 || latitude > 90 || longitude < -180 || longitude > 180) {
        showLocation('The browser returned invalid coordinates. Enter the district and continue without coordinates.');
      } else {
        capturedGeo = [Number(latitude.toFixed(6)), Number(longitude.toFixed(6))];
        const accuracy = Number.isFinite(position.coords.accuracy) ? ` Browser accuracy for this reading: approximately ${Math.round(position.coords.accuracy)} metres.` : '';
        showLocation(`Recorded latitude ${capturedGeo[0].toFixed(5)}, longitude ${capturedGeo[1].toFixed(5)}.${accuracy}`);
        markDirty();
      }
      $('record-location').disabled = false;
    }, failure => {
      const reason = failure?.code === 1 ? 'Location permission was denied.' : failure?.code === 3 ? 'The location request timed out.' : 'The device location could not be read.';
      showLocation(`${reason} Enter the district and continue without coordinates, or retry.`);
      $('record-location').disabled = false;
    }, {enableHighAccuracy:true,timeout:10000,maximumAge:0});
  };
  $('clear-location').onclick = () => { capturedGeo = null; showLocation(); markDirty(); };
  showLocation();
  if (priorCount) {
    selectValue('lane', initial.lane);
    $('region').value = initial.region || '';
    $('concerns').value = Array.isArray(initial.concerns) ? initial.concerns.join(', ') : initial.concerns || '';
    const context = initial.context || {};
    selectValue('context-category', context.category || 'unknown');
    selectValue('context-bundle', context.bundle_type || 'unknown');
    selectValue('context-shape', context.shape || 'unknown');
    selectValue('context-origin', typeof context.is_imported === 'boolean' ? (context.is_imported ? 'imported' : 'domestic') : 'unknown');
  }
  // Prefilling a prior value never makes a new officer attestation.
  $('confirm-context').checked = false;
  document.querySelector('[name="complete"]').checked = false;
  const available = () => {
    $('analyze-button').disabled = uploading || runningJob || draftLoading || draftSaving || items.some(item => item.checking || !item.image || item.invalid);
    if (draftsEnabled) $('save-draft').disabled = uploading || draftLoading || draftSaving || items.some(item => item.checking || !item.image || item.invalid);
  };
  const originalSize = item => item.rotation % 180 ? [item.height, item.width] : [item.width, item.height];
  const canvasFor = (item, full = false) => {
    const rotated = document.createElement('canvas'), context = rotated.getContext('2d');
    const sideways = item.rotation % 180 !== 0;
    rotated.width = sideways ? item.image.height : item.image.width;
    rotated.height = sideways ? item.image.width : item.image.height;
    context.translate(rotated.width / 2, rotated.height / 2);
    context.rotate(item.rotation * Math.PI / 180);
    context.drawImage(item.image, -item.image.width / 2, -item.image.height / 2);
    if (full || !item.crop) return rotated;
    const [x0,y0,x1,y1] = item.crop, result = document.createElement('canvas');
    result.width = Math.max(1, Math.round((x1-x0) * rotated.width));
    result.height = Math.max(1, Math.round((y1-y0) * rotated.height));
    result.getContext('2d').drawImage(rotated, x0*rotated.width, y0*rotated.height, (x1-x0)*rotated.width, (y1-y0)*rotated.height, 0, 0, result.width, result.height);
    return result;
  };
  const button = (label, action) => {
    const result = document.createElement('button');
    result.type = 'button'; result.className = 'ghost compact'; result.textContent = label; result.onclick = action;
    return result;
  };
  function render() {
    $('capture-list').replaceChildren();
    $('image-count').textContent = `${priorCount + items.length} / 12 total images${priorCount ? ` (${priorCount} retained + ${items.length} new)` : ''}`;
    items.forEach((item, index) => {
      const card = document.createElement('article'); card.className = 'capture-item';
      if (item.image) {
        const canvas = canvasFor(item); canvas.setAttribute('aria-label', `Preview ${index+1}: ${item.file.name}`); card.append(canvas);
      }
      const name = document.createElement('p'); name.textContent = `${index+1}. ${item.file.name}`; card.append(name);
      const label = document.createElement('label'); label.textContent = 'Package panel'; label.htmlFor = `panel-${index}`;
      const select = document.createElement('select'); select.id = `panel-${index}`;
      panelOptions.forEach(([value,text]) => select.add(new Option(text,value,false,item.panel===value)));
      select.onchange = () => { item.panel = select.value; markDirty(); }; card.append(label, select);
      const actions = document.createElement('div'); actions.className = 'row';
      if (item.image && !item.invalid) {
        actions.append(button('Rotate', () => { item.rotation = (item.rotation+90)%360; item.crop = null; markDirty(); checkQuality(item); }),
          button('Crop / zoom', () => openEditor(item)));
      }
      actions.append(button('Remove', () => { items.splice(items.indexOf(item), 1); URL.revokeObjectURL(item.url); markDirty(); render(); }));
      card.append(actions);
      const state = document.createElement('p'); state.setAttribute('role', 'status');
      if (item.checking) state.textContent = 'Checking image quality…';
      else if (item.qualityError) state.textContent = item.qualityError;
      else if (item.quality) state.textContent = item.quality.issues.length ? 'Review image guidance before analysis.' : 'No obvious quality issue found. Check that all printed text is readable.';
      card.append(state);
      if (item.qualityError && !item.invalid && !item.checking) card.append(button('Retry image check', () => checkQuality(item)));
      const guidance = document.createElement('ul'); guidance.className = 'quality-list';
      if (!item.checking && item.quality) item.quality.issues.forEach(issue => {
        const li = document.createElement('li'); li.textContent = `${issue.message} ${issue.action}`; guidance.append(li);
      });
      if (item.crop) { const li = document.createElement('li'); li.textContent = 'Cropped working image; original upload is preserved.'; guidance.append(li); }
      if (item.fallback) { const li = document.createElement('li'); li.textContent = 'Server-decoded preview. The original image is still used for the evidence upload.'; guidance.append(li); }
      card.append(guidance);
      if (item.quality && !item.checking) {
        const details = document.createElement('details'), summary = document.createElement('summary'), measurements = document.createElement('p');
        summary.textContent = 'Measured image indicators';
        const q = item.quality, metrics = q.metrics;
        const selected = metrics.selected_original_fraction == null ? '' : ` Selected area: ${Math.round(metrics.selected_original_fraction*100)}% of the original image.`;
        measurements.textContent = `${q.width} × ${q.height} pixels. Median brightness: ${metrics.median_brightness}/255. Brightness separation: ${metrics.contrast_p99_p01}/255. Denoised edge variance: ${metrics.denoised_laplacian_variance}.${selected} These are image statistics, not an accuracy score.`;
        details.append(summary, measurements); card.append(details);
      }
      $('capture-list').append(card);
    });
    available();
  }
  async function decoded(url) { const image = new Image(); image.src = url; await image.decode(); return image; }
  function checkQuality(item) {
    const generation = ++item.generation;
    item.checking = true; item.qualityError = ''; render();
    qualityQueue = qualityQueue.catch(() => {}).then(async () => {
      if (!items.includes(item) || generation !== item.generation) return;
      const form = new FormData(); form.append('file', item.file, item.file.name);
      form.append('edit', JSON.stringify({rotation:item.rotation,crop:item.crop}));
      try {
        const response = await fetch('/v1/capture/preview', {method:'POST', headers:{'X-CSRF-Token':window.tulaCSRF()}, body:form});
        if (response.status === 401) { location.assign('/login'); return; }
        const payload = await response.json();
        if (!response.ok) {
          const failure = new Error(typeof payload.detail === 'string' ? payload.detail : 'Image quality could not be checked. Retry shortly.');
          failure.status = response.status; throw failure;
        }
        if (!items.includes(item) || generation !== item.generation) return;
        // Reuse the bounded server image for an unedited display, including
        // HEIC fallback. Display pixels never become the evidence upload or
        // replace the original crop-coordinate dimensions.
        if (!item.image || (!item.previewReady && item.rotation === 0 && !item.crop)) {
          item.fallback = !item.image;
          item.image = await decoded(payload.preview);
          item.previewReady = true;
        }
        [item.width, item.height] = payload.original_size;
        item.quality = payload.quality;
      } catch (failure) {
        if (generation !== item.generation) return;
        item.invalid = [400,413,415,422].includes(failure.status);
        item.qualityError = item.invalid ? failure.message : 'Image check unavailable. Retry the check; final analysis also assesses image quality.';
        if (!item.image) item.qualityError += ' This format needs a server preview before you can frame it.';
      } finally {
        if (generation === item.generation) item.checking = false;
        if (items.includes(item)) render();
      }
    });
    return qualityQueue;
  }
  function addFiles(files) {
    const incoming = Array.from(files);
    fileQueue = fileQueue.catch(() => {}).then(async () => {
      error('');
      for (const file of incoming) {
        if (priorCount + items.length >= 12) { error('A linked package can have at most 12 retained and new images combined.'); break; }
        if (file.size > 25*1024*1024) { error(`${file.name} is larger than 25 MB.`); continue; }
        const url = URL.createObjectURL(file);
        const item = {file,url,image:null,width:0,height:0,rotation:0,crop:null,generation:0,checking:true,quality:null,qualityError:'',invalid:false,
          panel:priorCount ? 'unknown' : items.length===0 ? 'pdp' : items.length===1 ? 'back' : 'unknown'};
        try {
          item.image = await decoded(url);
          item.width = item.image.width; item.height = item.image.height;
          if (item.width*item.height > 25000000 || Math.min(item.width,item.height) < 16) {
            URL.revokeObjectURL(url); error(`${file.name}: use at least 16 pixels per side and at most 25 megapixels.`); continue;
          }
        } catch (_) { /* HEIC and other supported files get a server decode. */ }
        items.push(item); markDirty(); await checkQuality(item);
      }
      render();
    });
    return fileQueue;
  }
  $('files').onchange = async event => { await addFiles(event.target.files); event.target.value = ''; };
  $('dropzone').ondragover = event => { event.preventDefault(); $('dropzone').classList.add('dragover'); };
  $('dropzone').ondragleave = () => $('dropzone').classList.remove('dragover');
  $('dropzone').ondrop = event => { event.preventDefault(); $('dropzone').classList.remove('dragover'); addFiles(event.dataTransfer.files); };
  const coordinateInputs = ['crop-left','crop-top','crop-right','crop-bottom'].map($);
  function writeCoordinates() {
    const [width,height] = originalSize(editor), bounds = selection || [0,0,1,1];
    coordinateInputs.forEach((input,index) => { input.max = index%2 ? height : width; input.value = Math.round(bounds[index]*(index%2 ? height : width)); });
    $('crop-dimensions').textContent = `${width} × ${height} original pixels after rotation. The displayed preview may be scaled.`;
  }
  function drawEditor() {
    const original = canvasFor(editor,true), canvas = $('edit-canvas'); canvas.width = original.width; canvas.height = original.height;
    const context = canvas.getContext('2d'); context.drawImage(original,0,0);
    if (selection) { const [x0,y0,x1,y1] = selection; context.strokeStyle = '#bd6227'; context.lineWidth = Math.max(3,canvas.width/250); context.strokeRect(x0*canvas.width,y0*canvas.height,(x1-x0)*canvas.width,(y1-y0)*canvas.height); context.fillStyle='#cd8b2233'; context.fillRect(x0*canvas.width,y0*canvas.height,(x1-x0)*canvas.width,(y1-y0)*canvas.height); }
  }
  function readCoordinates() {
    const [width,height] = originalSize(editor), values = coordinateInputs.map(input => input.value === '' ? NaN : Number(input.value));
    const [left,top,right,bottom] = values;
    const valid = values.every(Number.isInteger) && left>=0 && top>=0 && right<=width && bottom<=height && right-left>=16 && bottom-top>=16;
    $('crop-error').textContent = valid ? '' : 'Use whole-pixel bounds inside the image, with at least 16 pixels in both directions.';
    $('apply-crop').disabled = !valid;
    if (valid) { selection = [left/width,top/height,right/width,bottom/height]; drawEditor(); }
    return valid;
  }
  coordinateInputs.forEach(input => { input.oninput = readCoordinates; });
  function openEditor(item) {
    editor = item; selection = item.crop; pointer = null;
    $('edit-zoom').value = 100; $('edit-canvas').style.maxWidth = '100%'; $('crop-error').textContent = ''; $('apply-crop').disabled = false;
    writeCoordinates(); drawEditor(); $('edit-dialog').showModal();
  }
  const point = event => { const rect=$('edit-canvas').getBoundingClientRect(); return [Math.max(0,Math.min(1,(event.clientX-rect.left)/rect.width)),Math.max(0,Math.min(1,(event.clientY-rect.top)/rect.height))]; };
  $('edit-canvas').onpointerdown = event => { pointer=point(event); $('edit-canvas').setPointerCapture(event.pointerId); };
  $('edit-canvas').onpointermove = event => {
    if (!pointer) return; const end=point(event); selection=[Math.min(end[0],pointer[0]),Math.min(end[1],pointer[1]),Math.max(end[0],pointer[0]),Math.max(end[1],pointer[1])];
    writeCoordinates(); drawEditor(); readCoordinates();
  };
  $('edit-canvas').onpointerup = () => { pointer=null; };
  $('edit-canvas').onpointercancel = () => { pointer=null; };
  $('edit-zoom').oninput = event => { $('edit-canvas').style.maxWidth = `${event.target.value}%`; };
  $('apply-crop').onclick = () => { if (readCoordinates()) { editor.crop=selection; markDirty(); checkQuality(editor); $('edit-dialog').close(); } };
  $('reset-crop').onclick = () => { editor.crop=null; selection=null; markDirty(); writeCoordinates(); drawEditor(); $('crop-error').textContent=''; $('apply-crop').disabled=false; checkQuality(editor); };
  $('close-editor').onclick = () => $('edit-dialog').close();
  const closeCamera = () => { if(camera) camera.getTracks().forEach(track=>track.stop()); camera=null; $('camera-dialog').close(); };
  $('close-camera').onclick = closeCamera; $('camera-dialog').addEventListener('cancel',closeCamera);
  $('open-camera').onclick = async () => {
    $('camera-error').textContent=''; $('camera-dialog').showModal();
    try { if(!navigator.mediaDevices?.getUserMedia) throw new Error('Camera capture is unavailable here. Use the file picker or open the app over HTTPS.'); camera=await navigator.mediaDevices.getUserMedia({video:{facingMode:{ideal:'environment'},width:{ideal:1920},height:{ideal:1080}},audio:false}); $('camera-video').srcObject=camera; }
    catch(failure) { $('camera-error').textContent=failure.message || 'Camera access was denied. Use image upload instead.'; }
  };
  $('take-photo').onclick = () => {
    const video=$('camera-video'); if(!video.videoWidth) return; const canvas=document.createElement('canvas'); canvas.width=video.videoWidth; canvas.height=video.videoHeight; canvas.getContext('2d').drawImage(video,0,0);
    canvas.toBlob(blob=>{ if(blob) addFiles([new File([blob],`package-${Date.now()}.jpg`,{type:'image/jpeg'})]); closeCamera(); },'image/jpeg',.95);
  };
  const stages=[['received','Images received and validated'],['ocr','Image quality, label detection and OCR'],['extraction','Structured declarations extracted'],['applicability','Product context and applicable rules'],['measurement','Scale and typography checked'],['rules','Compliance analysis'],['saved','Inspection saved for officer review']];
  function showProgress(job) {
    runningJob = !['complete','failed'].includes(job.state);
    $('capture-progress').hidden=false; $('progress-title').textContent=job.state==='complete'?'Ready for evidence review':job.state==='failed'?'Analysis needs attention':'Analyzing package'; $('progress-detail').textContent=job.detail;
    const current=stages.findIndex(([key])=>key===job.stage); $('process-list').replaceChildren();
    stages.forEach(([key,label],index)=>{ const li=document.createElement('li'); li.textContent=label; li.className=job.state==='complete'||index<current?'done':index===current?'current':''; $('process-list').append(li); });
    $('process-time').textContent=`Submitted ${new Date(job.created_at).toLocaleTimeString()}. CPU processing may take longer for difficult or multiple images. You may leave this page and return.`;
    $('progress-result').replaceChildren();
    if(job.state==='complete') { localStorage.removeItem('tula-job'); const link=document.createElement('a'); link.className='button-link'; link.href=`/inspections/${job.scan_id}`; link.textContent='Review inspection →'; $('progress-result').append(link); }
    else if(job.state==='failed') {
      const message=document.createElement('p'); message.className='error-box'; message.textContent=job.error;
      $('progress-result').append(message,button('Retry saved images',async()=>{const response=await fetch(`/v1/jobs/${job.id}/retry`,{method:'POST',headers:{'X-CSRF-Token':window.tulaCSRF()}});if(response.ok)poll(job.id);else error('Retry could not be started. Reload and try again.');}));
    }
    available();
  }
  async function poll(id) {
    clearTimeout(pollTimer);
    try { const response=await fetch(`/v1/jobs/${encodeURIComponent(id)}`); if(response.status===401){location.assign('/login');return;} if(response.status===404){localStorage.removeItem('tula-job');runningJob=false;$('capture-progress').hidden=true;error('This saved processing job is unavailable for your account. Open Processing to find your saved work.');available();return;} if(!response.ok)throw new Error(); const job=await response.json();showProgress(job);if(!['complete','failed'].includes(job.state))pollTimer=setTimeout(()=>poll(id),1500); }
    catch(_) { $('progress-detail').textContent='Connection interrupted. Checking the saved processing job again…';pollTimer=setTimeout(()=>poll(id),5000); }
  }
  function draftDetails() {
    const dimensions = {};
    if ($('pdp-width').value) dimensions.pdp_width_mm = Number($('pdp-width').value);
    if ($('pdp-height').value) dimensions.pdp_height_mm = Number($('pdp-height').value);
    const context = {category:$('context-category').value,bundle_type:$('context-bundle').value,shape:$('context-shape').value};
    if ($('context-origin').value !== 'unknown') context.is_imported = $('context-origin').value === 'imported';
    return {title:$('draft-title').value,lane:$('lane').value,region:$('region').value,geo:capturedGeo,concerns:$('concerns').value,
      parent_scan_id:$('parent-scan-id').value,
      parent_revision:$('parent-scan-id').value && $('parent-revision')?.value !== '' ? Number($('parent-revision')?.value) : null,
      rescan_target:$('parent-scan-id').value ? ($('rescan-target')?.value || '') : '',dimensions,context};
  }
  async function draftResponse(response) {
    if (response.status === 401) { location.assign('/login'); throw new Error('Sign in again to open your saved drafts.'); }
    const payload = await response.json();
    if (!response.ok) throw new Error(typeof payload.detail === 'string' ? payload.detail : 'The capture draft request failed. Check your fields and retry.');
    return payload;
  }
  async function refreshDrafts() {
    try {
      const payload = await draftResponse(await fetch('/v1/capture/drafts'));
      $('draft-list').replaceChildren();
      if (!payload.drafts.length) { const empty = document.createElement('p'); empty.className='small muted'; empty.textContent='No saved capture drafts.'; $('draft-list').append(empty); }
      payload.drafts.forEach(draft => {
        const row = document.createElement('div'), title = document.createElement('p'), actions = document.createElement('div'), link = document.createElement('a');
        row.className='card'; actions.className='row';
        title.textContent=`${draft.title} · ${draft.image_count} image(s) · revision ${draft.revision} · expires ${new Date(draft.expires_at*1000).toLocaleDateString()}`;
        // A new tab preserves any currently unsaved local capture.
        link.href=`/?draft=${encodeURIComponent(draft.id)}`; link.target='_blank'; link.rel='noopener'; link.className='button-link secondary'; link.textContent='Resume in new tab';
        actions.append(link, button('Delete saved draft', async () => {
          const remove = actions.children[1]; remove.disabled=true;
          try {
            await draftResponse(await fetch(`/v1/capture/drafts/${draft.id}?revision=${draft.revision}`,{method:'DELETE',headers:{'X-CSRF-Token':window.tulaCSRF()}}));
            if (activeDraft?.id === draft.id) { activeDraft=null; saveAttempt=null; savedGeneration=-1; }
            draftMessage('Saved draft deleted. Images already loaded in this tab and accepted inspections are unchanged.');
            await refreshDrafts();
          } catch (failure) { draftMessage(failure.message); remove.disabled=false; }
        }));
        row.append(title,actions); $('draft-list').append(row);
      });
    } catch (failure) { draftMessage(failure.message || 'Saved drafts could not be loaded. Retry the saved list.'); }
  }
  async function saveDraft() {
    if (draftSaving || draftLoading || uploading) return;
    if (!items.length) { draftMessage('Add at least one image before saving a capture draft.'); return; }
    if (items.some(item=>item.checking||!item.image||item.invalid)) { draftMessage('Finish image checks and replace invalid images before saving.'); return; }
    if (items.reduce((sum,item)=>sum+item.file.size,0) > 100*1024*1024) { draftMessage('Draft originals exceed the 100 MB limit. Remove an image before saving.'); return; }
    const generation = draftGeneration, id = activeDraft?.id || saveAttempt?.id || crypto.randomUUID().replaceAll('-','');
    if (!saveAttempt || saveAttempt.generation !== generation || saveAttempt.id !== id) saveAttempt={id,generation,token:crypto.randomUUID().replaceAll('-','')};
    const data = new FormData();
    data.set('revision',String(activeDraft?.revision || 0)); data.set('save_token',saveAttempt.token);
    data.set('details',JSON.stringify(draftDetails()));
    items.forEach(item=>data.append('files',item.file,item.file.name));
    data.set('panels',items.map(item=>item.panel).join(',')); data.set('edits',JSON.stringify(items.map(({rotation,crop})=>({rotation,crop}))));
    draftSaving=true; available(); draftMessage('Saving original images and capture edits…');
    try {
      const result=await draftResponse(await fetch(`/v1/capture/drafts/${id}`,{method:'POST',headers:{'X-CSRF-Token':window.tulaCSRF()},body:data}));
      activeDraft=result; savedGeneration=generation; saveAttempt=null;
      draftMessage(draftGeneration===generation ? `Capture draft saved · revision ${result.revision}. Resume it from My saved drafts. Analyze does not delete it.` : `Revision ${result.revision} was saved. Newer changes in this tab are still unsaved.`);
      if (!runningJob) window.history?.replaceState(null,'',`/?draft=${encodeURIComponent(result.id)}`);
      await refreshDrafts();
    } catch (failure) { draftMessage(failure.message || 'Draft save failed. Your images remain in this tab; retry Save capture draft.'); }
    finally { draftSaving=false; available(); }
  }
  async function resumeDraft(id) {
    if (!/^[a-f0-9]{32}$/.test(id)) { draftMessage('This capture draft link is invalid. Choose one from My saved drafts.'); return; }
    draftLoading=true; $('capture-form').inert=true; available(); draftMessage('Verifying and loading saved original images…');
    try {
      const draft=await draftResponse(await fetch(`/v1/capture/drafts/${id}`));
      const restored=[];
      // Fetch all revision-fenced originals before replacing any local state.
      for (const image of draft.images) {
        const response=await fetch(image.url);
        if (!response.ok) { await draftResponse(response); }
        restored.push(new File([await response.blob()],image.filename,{type:image.mime}));
      }
      items.forEach(item=>URL.revokeObjectURL(item.url)); items.splice(0); priorCount=draft.prior_count;
      const details=draft.details, context=details.context;
      $('parent-scan-id').value=details.parent_scan_id; $('draft-title').value=details.title;
      if ($('parent-revision')) {
        $('parent-revision').value=Number.isInteger(details.parent_revision) ? String(details.parent_revision) : '';
        $('parent-revision').disabled=!details.parent_scan_id;
      }
      if ($('rescan-target')) { selectValue('rescan-target',details.rescan_target || ''); $('rescan-target').disabled=!details.parent_scan_id; }
      if ($('rescan-context')) $('rescan-context').hidden=!details.parent_scan_id;
      if ($('rescan-parent-reference')) $('rescan-parent-reference').textContent=details.parent_scan_id ? `Original inspection ${details.parent_scan_id} · revision ${details.parent_revision ?? 'not recorded'}. Its rule version and assessment basis will be retained.` : '';
      selectValue('lane',details.lane); $('region').value=details.region; $('concerns').value=details.concerns;
      capturedGeo=Array.isArray(details.geo)&&details.geo.length===2 ? details.geo.map(Number) : null; showLocation();
      selectValue('context-category',context.category); selectValue('context-bundle',context.bundle_type); selectValue('context-shape',context.shape);
      selectValue('context-origin',typeof context.is_imported==='boolean' ? (context.is_imported?'imported':'domestic') : 'unknown');
      $('pdp-width').value=details.dimensions.pdp_width_mm || ''; $('pdp-height').value=details.dimensions.pdp_height_mm || '';
      $('confirm-context').checked=false; document.querySelector('[name="complete"]').checked=false;
      // Decode unedited originals first. In particular a HEIC fallback must not
      // apply saved rotation/crop twice to an already edited server preview.
      await addFiles(restored);
      if (items.length!==draft.images.length) throw new Error('Some saved images could not be loaded. The saved draft remains available; reload to retry.');
      for (let index=0;index<items.length;index++) {
        const source=draft.images[index], item=items[index];
        item.panel=source.panel; item.rotation=source.rotation; item.crop=source.crop;
        if (item.rotation || item.crop) await checkQuality(item);
      }
      activeDraft=draft; savedGeneration=draftGeneration; saveAttempt=null;
      draftMessage(`Resumed revision ${draft.revision}${priorCount ? ` with ${priorCount} retained parent image(s)` : ''}. Review the images and confirm package context and complete coverage again.`);
      render();
    } catch (failure) { draftMessage(failure.message || 'The saved draft could not be resumed. It has not been deleted.'); }
    finally { draftLoading=false; $('capture-form').inert=false; available(); }
  }
  if (draftsEnabled) {
    $('save-draft').onclick=saveDraft;
    $('refresh-drafts').onclick=refreshDrafts;
    $('capture-form').addEventListener('input',markDirty);
    $('capture-form').addEventListener('change',markDirty);
    window.addEventListener('beforeunload',event=>{
      if (items.length && draftGeneration!==savedGeneration && !runningJob && !draftLoading) { event.preventDefault(); event.returnValue=''; }
    });
    refreshDrafts();
    const requestedDraft=$('capture-drafts').dataset.requestedDraft || '';
    if (requestedDraft) resumeDraft(requestedDraft);
  }
  $('capture-form').onsubmit = async event => {
    event.preventDefault();error('');
    if (draftLoading || draftSaving) { error('Wait for the capture draft request to finish before analyzing.'); return; }
    if(!items.length){error('Add at least one package image before analyzing.');return;}
    if(items.some(item=>item.checking||!item.image||item.invalid)){error('Finish the image checks and replace any invalid image before analysis.');return;}
    if(priorCount+items.length>12){error('A linked inspection may contain at most 12 retained and new images combined.');return;}
    const data=new FormData(event.target);data.delete('files');
    data.set('geo',capturedGeo ? JSON.stringify(capturedGeo) : '');
    if (!$('parent-scan-id').value) { data.delete('parent_revision'); data.delete('rescan_target'); }
    // Always submit the original File, never a JPEG/canvas preview.
    items.forEach(item=>data.append('files',item.file,item.file.name));data.set('panels',items.map(item=>item.panel).join(','));data.set('edits',JSON.stringify(items.map(({rotation,crop})=>({rotation,crop}))));
    const dimensions={};if($('pdp-width').value)dimensions.pdp_width_mm=Number($('pdp-width').value);if($('pdp-height').value)dimensions.pdp_height_mm=Number($('pdp-height').value);data.set('dimensions',JSON.stringify(dimensions));
    const confirmed=$('confirm-context').checked,category=$('context-category').value,bundle=$('context-bundle').value,origin=$('context-origin').value,shape=$('context-shape').value;
    const context={category,category_confirmed:confirmed&&category!=='unknown',bundle_type:bundle,bundle_confirmed:confirmed&&bundle!=='unknown',shape,shape_confirmed:confirmed&&shape!=='unknown',imported_confirmed:confirmed&&origin!=='unknown'};if(origin!=='unknown')context.is_imported=origin==='imported';data.set('legal_context',JSON.stringify(context));
    uploading=true;available();$('analyze-button').textContent='Uploading images…';
    try { const response=await fetch('/v1/inspections',{method:'POST',headers:{'X-CSRF-Token':window.tulaCSRF()},body:data});const result=await response.json();if(!response.ok)throw new Error(typeof result.detail==='string'?result.detail:'Check the inspection fields.');localStorage.setItem('tula-job',result.id);window.history?.replaceState(null,'',`/?job=${encodeURIComponent(result.id)}`);showProgress(result);$('capture-progress').focus();poll(result.id); }
    catch(failure){error(failure.message||'Upload failed. Check your connection and try again.');}
    finally{uploading=false;$('analyze-button').textContent='Analyze package →';available();}
  };
  render();
  const requested=initial.requested_job || '';
  const pending=requested || localStorage.getItem('tula-job');
  if(requested && !/^[a-f0-9]{32}$/.test(requested))error('This processing link is invalid. Open Processing to find your saved work.');
  else if(pending){if(requested)localStorage.setItem('tula-job',requested);runningJob=true;$('capture-progress').hidden=false;$('progress-title').textContent='Opening saved processing job';$('progress-detail').textContent='Loading the last recorded processing stage…';available();poll(pending);}
})();
