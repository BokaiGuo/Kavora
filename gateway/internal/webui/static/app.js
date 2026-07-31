const $ = id => document.getElementById(id);
const output = $('output');
const send = $('send');
const stream = $('stream');

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
    $('request-id').textContent = 'REQUEST ID ' + (response.headers.get('X-Request-ID') || '—');
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
  }
};

health();
