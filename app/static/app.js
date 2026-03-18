let timerInterval = null;
function startTimer(seconds){
  const el = document.getElementById('timer-display');
  if(!el) return;
  let remaining = seconds;
  clearInterval(timerInterval);
  const render = () => {
    const m = Math.floor(remaining/60);
    const s = String(remaining%60).padStart(2,'0');
    el.textContent = `Rest timer: ${m}:${s}`;
    if(remaining <= 0){
      clearInterval(timerInterval);
      el.textContent = 'Rest timer: done';
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
}
