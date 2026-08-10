const $ = id => document.getElementById(id);
const output = $('output');
const send = $('send');
const stream = $('stream');
let latestRequestID = '';

function adminHeaders() {
  const token = $('admin-token').value.trim();
  return token ? { Authorization: 'Bearer ' + token } : {};
}

async function lifecycle() {
  try {
    const response = await fetch('/v1/admin/lifecycle', { headers: adminHeaders() });
    if (!response.ok) throw Error();
    const state = await response.json();
    const approval = state.approval_required ? (state.approved ? ` · APPROVED ${state.approved_by}` : ' · APPROVAL REQUIRED') : '';
    $('lifecycle-state').textContent = `${state.stage.toUpperCase()} · ${Math.round((state.canary_fraction || 0) * 100)}%${approval}`;
  } catch {
    $('lifecycle-state').textContent = 'LIFECYCLE —';
  }
}

async function approveCanary() {
  const response = await fetch('/v1/admin/lifecycle/approve', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...adminHeaders() },
    body: JSON.stringify({ approved_by: $('operator').value.trim() }),
  });
  if (!response.ok) throw Error(await response.text());
  await lifecycle();
}

function renderDecision(decision) {
  const requirements = Object.entries(decision.requirements || {}).map(([key, value]) => `${key}=${value}`).join(' · ') || 'none';
  const rows = (decision.candidates || []).map(candidate => `<tr class="${candidate.eligible ? '' : 'excluded'}"><td>${candidate.backend_id}</td><td>${candidate.eligible ? 'yes' : 'no'}</td><td>${candidate.cache_source} / ${candidate.cache_quality}</td><td>${(candidate.cache_confidence || 0).toFixed(3)}</td><td>${candidate.matched_tokens}</td><td>${(candidate.queue_depth || 0).toFixed(2)}</td><td>${(candidate.predicted_ttft_ms || 0).toFixed(1)}</td><td>${(candidate.score || 0).toFixed(2)}</td><td>${candidate.excluded_by?.join(', ') || candidate.reason}</td></tr>`).join('');
  $('decision-inspector').innerHTML = `<div class="decision-summary"><span>MODE <strong>${decision.mode}</strong></span><span>SELECTED <strong>${decision.selected || 'static fallback'}</strong></span><span>ACTUAL <strong>${decision.actual_selected || 'pending'}</strong></span><span>REASON <strong>${decision.reason}</strong></span><span>REQUIREMENTS <strong>${requirements}</strong></span></div><div class="decision-table-wrap"><table><thead><tr><th>Backend</th><th>Eligible</th><th>Evidence</th><th>Confidence</th><th>Matched</th><th>Queue</th><th>TTFT ms</th><th>Score</th><th>Explanation</th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

async function inspectDecision(requestID = latestRequestID) {
  if (!requestID) return;
  try {
    const response = await fetch('/v1/admin/decisions/' + encodeURIComponent(requestID), { headers: adminHeaders() });
    if (!response.ok) throw Error(await response.text());
    renderDecision(await response.json());
  } catch (error) {
    $('decision-inspector').textContent = 'Decision unavailable: ' + error.message;
  }
}

function setDecision(text, kind) {
  const decision = $('decision');
  decision.textContent = text;
  decision.className = 'decision ' + kind;
}

async function health() {
  try {
    const response = await fetch('/healthz');
    if (!response.ok) throw Error();
    $('health-label').textContent = 'Gateway online';
    $('health-detail').textContent = 'Gateway ready';
  } catch {
    $('health-label').textContent = 'Gateway unavailable';
    $('health-detail').textContent = 'Start the gateway first';
  }
}

async function readStream(response) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split('\n\n');
    buffer = events.pop();
    for (const event of events) {
      const line = event.split('\n').find(value => value.startsWith('data:'));
      if (!line) continue;
      const data = line.slice(5).trim();
      if (data === '[DONE]') continue;
      try {
        const json = JSON.parse(data);
        const chunk = json.choices?.[0]?.delta?.content || '';
        output.textContent += chunk;
        output.scrollTop = output.scrollHeight;
      } catch {
        // The Rust policy engine rejects malformed events server-side.
      }
    }
  }
}

send.onclick = async () => {
  const message = $('message').value.trim();
  if (!message) {
    $('message').focus();
    return;
  }
  send.disabled = true;
  output.textContent = '';
  setDecision('CHECKING', '');
  $('response-meta').textContent = 'Policy evaluation in progress';
  $('request-id').textContent = 'REQUEST ID —';
  const started = performance.now();
  const body = { model: $('model').value.trim() || 'demo-model', messages: [] };
  if ($('system').value.trim()) body.messages.push({ role: 'system', content: $('system').value.trim() });
  body.messages.push({ role: 'user', content: message });
  body.stream = stream.checked;
  const headers = { 'Content-Type': 'application/json' };
  if ($('api-key').value.trim()) headers.Authorization = 'Bearer ' + $('api-key').value.trim();
  try {
    const response = await fetch('/v1/chat/completions', { method: 'POST', headers, body: JSON.stringify(body) });
    latestRequestID = response.headers.get('X-Request-ID') || '';
    $('request-id').textContent = 'REQUEST ID ' + (latestRequestID || '—');
    if (!response.ok) {
      const error = await response.json().catch(() => ({ error: { message: response.statusText } }));
      setDecision('BLOCKED', 'block');
      output.textContent = error.error?.message || 'Request rejected';
      $('response-meta').textContent = error.error?.code || 'Gateway error';
      return;
    }
    setDecision('ALLOWED', 'allow');
    if (stream.checked) {
      await readStream(response);
    } else {
      const json = await response.json();
      output.textContent = json.choices?.[0]?.message?.content || JSON.stringify(json, null, 2);
    }
    $('response-meta').textContent = 'Response delivered through policy plane';
  } catch (error) {
    setDecision('ERROR', 'block');
    output.textContent = error.message;
    $('response-meta').textContent = 'Could not reach gateway';
  } finally {
    $('latency').textContent = Math.round(performance.now() - started) + ' ms';
    send.disabled = false;
    await inspectDecision();
    await lifecycle();
  }
};

$('refresh-decision').onclick = () => inspectDecision();
$('approve-canary').onclick = () => approveCanary().catch(error => { $('decision-inspector').textContent = 'Approval failed: ' + error.message; });
$('admin-token').onchange = () => { inspectDecision(); lifecycle(); };

health();
lifecycle();
