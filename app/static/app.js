let timerInterval = null;
let timerAudioContext = null;

function setStatus(el, text, cls){
  if(!el) return;
  el.textContent = text;
  el.classList.remove('saving', 'saved', 'error');
  if(cls) el.classList.add(cls);
}

function beepTimerDone(){
  try {
    const Ctx = window.AudioContext || window.webkitAudioContext;
    if(!Ctx) return;
    timerAudioContext = timerAudioContext || new Ctx();
    const now = timerAudioContext.currentTime;
    [0, 0.18].forEach((offset) => {
      const osc = timerAudioContext.createOscillator();
      const gain = timerAudioContext.createGain();
      osc.type = 'sine';
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.001, now + offset);
      gain.gain.exponentialRampToValueAtTime(0.18, now + offset + 0.01);
      gain.gain.exponentialRampToValueAtTime(0.001, now + offset + 0.16);
      osc.connect(gain).connect(timerAudioContext.destination);
      osc.start(now + offset);
      osc.stop(now + offset + 0.18);
    });
  } catch (err) {
    console.warn('Timer beep failed', err);
  }
}

function notifyTimerDone(){
  beepTimerDone();
  if(navigator.vibrate) navigator.vibrate([200, 100, 200]);
}

function startTimer(seconds){
  const el = document.getElementById('timer-display');
  if(!el) return;
  let remaining = Number(seconds) || 0;
  clearInterval(timerInterval);
  const render = () => {
    const m = Math.floor(Math.max(remaining, 0)/60);
    const s = String(Math.max(remaining, 0)%60).padStart(2,'0');
    el.textContent = `Rest timer: ${m}:${s}`;
    if(remaining <= 0){
      clearInterval(timerInterval);
      el.textContent = 'Rest timer: done';
      notifyTimerDone();
      return;
    }
    remaining -= 1;
  };
  render();
  timerInterval = setInterval(render, 1000);
}

function repeatLastRow(button){
  const form = button.closest('form');
  if(!form) return;
  const weightInputs = [...form.querySelectorAll('input[name^="weight_"]')];
  const repInputs = [...form.querySelectorAll('input[name^="reps_"]')];
  const rpeInputs = [...form.querySelectorAll('input[name^="rpe_"]')];
  if(weightInputs.length < 2) return;
  const srcW = weightInputs[weightInputs.length-2]?.value || '';
  const srcR = repInputs[repInputs.length-2]?.value || '';
  const srcE = rpeInputs[rpeInputs.length-2]?.value || '';
  weightInputs[weightInputs.length-1].value = srcW;
  repInputs[repInputs.length-1].value = srcR;
  rpeInputs[rpeInputs.length-1].value = srcE;
  form.dispatchEvent(new Event('input', { bubbles: true }));
}

function refreshExerciseProgress(){
  document.querySelectorAll('.exercise-card').forEach((card) => {
    const doneBoxes = [...card.querySelectorAll('input[type="checkbox"][name^="done_"]')];
    const completed = doneBoxes.filter((box) => box.checked).length;
    const total = doneBoxes.length;
    const isComplete = total > 0 && completed === total;
    card.classList.toggle('exercise-complete', isComplete);
    card.classList.toggle('exercise-pending', !isComplete);
    const status = card.querySelector('.exercise-status');
    if(status){
      status.textContent = isComplete ? 'Done' : `${completed}/${total} sets done`;
      status.classList.toggle('complete', isComplete);
      status.classList.toggle('pending', !isComplete);
    }
    const chip = document.querySelector(`.exercise-progress-chip[href="#${card.id}"]`);
    if(chip){
      chip.classList.toggle('complete', isComplete);
      chip.classList.toggle('pending', !isComplete);
      const count = chip.querySelector('.exercise-progress-count');
      if(count) count.textContent = `${completed}/${total}`;
    }
  });
}

function autosaveForm(form){
  const globalStatus = document.getElementById('autosave-status');
  const localStatus = form.querySelector('.exercise-save-status');
  setStatus(globalStatus, 'Saving…', 'saving');
  setStatus(localStatus, 'Saving…', 'saving');
  return fetch(form.action, {
    method: form.method || 'POST',
    body: new FormData(form),
    headers: {
      'X-Requested-With': 'XMLHttpRequest',
      'Accept': 'application/json'
    }
  })
    .then(async (resp) => {
      if(!resp.ok) throw new Error(`Save failed (${resp.status})`);
      return resp.json();
    })
    .then(() => {
      setStatus(globalStatus, 'Saved', 'saved');
      setStatus(localStatus, 'Saved', 'saved');
      window.setTimeout(() => {
        if(globalStatus?.textContent === 'Saved') setStatus(globalStatus, 'Autosave ready', '');
        if(localStatus?.textContent === 'Saved') setStatus(localStatus, '', '');
      }, 1200);
    })
    .catch((err) => {
      console.error(err);
      setStatus(globalStatus, 'Autosave failed', 'error');
      setStatus(localStatus, 'Autosave failed', 'error');
    });
}

function setupWorkoutAutosave(){
  const forms = [...document.querySelectorAll('form[data-autosave="true"]')];
  if(!forms.length) return;
  refreshExerciseProgress();
  forms.forEach((form) => {
    let timeout = null;
    let inFlight = Promise.resolve();
    const scheduleSave = () => {
      window.clearTimeout(timeout);
      timeout = window.setTimeout(() => {
        inFlight = inFlight.then(() => autosaveForm(form));
      }, 500);
    };

    form.addEventListener('input', scheduleSave);
    form.addEventListener('change', (event) => {
      if(event.target.matches('input[type="checkbox"][name^="done_"]') && event.target.checked){
        const restInput = form.querySelector('input[name="rest_seconds"]');
        startTimer(restInput?.value || form.dataset.restSeconds || 90);
      }
      refreshExerciseProgress();
      scheduleSave();
    });

    form.addEventListener('submit', (event) => {
      const submitter = event.submitter;
      if(submitter && submitter.hasAttribute('formaction')){
        return;
      }
      event.preventDefault();
      window.clearTimeout(timeout);
      inFlight = inFlight.then(() => autosaveForm(form));
    });
  });
}

function setupDeleteModal(){
  const modal = document.getElementById('confirm-modal');
  const text = document.getElementById('confirm-modal-text');
  const confirmBtn = document.getElementById('confirm-delete-btn');
  if(!modal || !text || !confirmBtn) return;
  let activeForm = null;
  document.querySelectorAll('.delete-form').forEach(form => {
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      activeForm = form;
      const name = form.dataset.workoutName || 'this workout';
      text.textContent = `Delete ${name}? This cannot be undone.`;
      modal.classList.remove('hidden');
      modal.setAttribute('aria-hidden', 'false');
    });
  });
  document.querySelectorAll('[data-close-modal="true"]').forEach(el => {
    el.addEventListener('click', () => {
      modal.classList.add('hidden');
      modal.setAttribute('aria-hidden', 'true');
      activeForm = null;
    });
  });
  confirmBtn.addEventListener('click', () => {
    if(activeForm) activeForm.submit();
  });
}

document.addEventListener('DOMContentLoaded', () => {
  setupDeleteModal();
  setupWorkoutAutosave();
});
