const express = require('express');
const { SSMClient, GetParameterCommand } = require('@aws-sdk/client-ssm');

const app = express();
const PORT = Number(process.env.PORT || 3000);
const WEATHER_URL = process.env.WEATHER_URL;
const GEMINI_BASE = process.env.GEMINI_API_BASE ||
  'https://generativelanguage.googleapis.com';
const MODEL = process.env.GEMINI_MODEL;
const KEY_PARAM = process.env.GEMINI_KEY_PARAM;
const KEY_CACHE_MS = Number(process.env.KEY_CACHE_SECONDS || 60) * 1000;
const ssm = new SSMClient({ region: process.env.AWS_REGION });

const log = (event, fields = {}) =>
  console.log(JSON.stringify({ ts: new Date().toISOString(), event, ...fields }));

let cachedKey = { value: null, at: 0 };

async function getApiKey() {
  if (cachedKey.value && Date.now() - cachedKey.at < KEY_CACHE_MS) {
    return cachedKey.value;
  }
  const result = await ssm.send(new GetParameterCommand({
    Name: KEY_PARAM,
    WithDecryption: true
  }), { abortSignal: AbortSignal.timeout(5000) });
  cachedKey = { value: result.Parameter.Value, at: Date.now() };
  return cachedKey.value;
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, character => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;'
  })[character]);
}

function fallback(forecast) {
  const temperature = forecast.temperatureUnit === 'C'
    ? forecast.temperature * 9 / 5 + 32
    : forecast.temperature;
  let advice;
  if (temperature < 32) {
    advice = 'Wear a warm coat, layers, a hat, and gloves.';
  } else if (temperature < 50) {
    advice = 'Wear a jacket with a warm layer underneath.';
  } else if (temperature < 65) {
    advice = 'Wear a light jacket or sweater.';
  } else {
    advice = 'Wear light, comfortable clothing.';
  }
  if (
    Number(forecast.precipitationChance) >= 30 ||
    /rain|showers|snow|storm/i.test(forecast.shortForecast)
  ) {
    advice += ' Bring waterproof outerwear and suitable shoes.';
  }
  return advice;
}

async function clothingAdvice(weather) {
  try {
    const key = await getApiKey();
    const prompt = {
      zip: weather.zip,
      city: weather.city,
      state: weather.state,
      forecast: weather.forecast
    };
    const response = await fetch(
      `${GEMINI_BASE}/v1beta/models/${encodeURIComponent(MODEL)}:generateContent`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-goog-api-key': key
        },
        signal: AbortSignal.timeout(10000),
        body: JSON.stringify({
          contents: [{
            parts: [{
              text: 'Suggest appropriate clothing in one short sentence ' +
                'using only this weather data. Return plain text: ' +
                JSON.stringify(prompt)
            }]
          }],
          generationConfig: { maxOutputTokens: 160 }
        })
      }
    );
    if (!response.ok) {
      log('gemini_error', { status: response.status, reason: 'http_error' });
      return { advice: fallback(weather.forecast), source: 'fallback' };
    }
    const data = await response.json();
    const text = data.candidates?.[0]?.content?.parts
      ?.map(part => part.text || '').join('').trim();
    if (!text) throw new Error('empty_answer');
    return { advice: text, source: 'gemini' };
  } catch (err) {
    // Log an error type, never the key or a raw API response.
    log('gemini_error', {
      status: 0,
      reason: err.message === 'empty_answer' ? 'empty_answer' : err.name
    });
    return { advice: fallback(weather.forecast), source: 'fallback' };
  }
}

async function getAdvice(zip) {
  if (!/^\d{5}$/.test(zip)) {
    return { status: 400, body: { error: 'invalid_zip' } };
  }

  let response;
  let weather;
  try {
    response = await fetch(
      `${WEATHER_URL}/weather?zip=${encodeURIComponent(zip)}`,
      { signal: AbortSignal.timeout(60000) }
    );
    weather = await response.json();
  } catch {
    log('advisor_error', { stage: 'weather', reason: 'request_failed' });
    return {
      status: 502,
      body: { error: 'weather_service_unavailable' }
    };
  }

  if (response.status === 400 || response.status === 404) {
    return { status: response.status, body: weather };
  }
  if (!response.ok || !weather.forecast) {
    log('advisor_error', { stage: 'weather', status: response.status });
    return {
      status: 502,
      body: { error: 'weather_service_unavailable' }
    };
  }

  const suggestion = await clothingAdvice(weather);
  log('advisor_success', { zip, source: suggestion.source });
  return { status: 200, body: { ...weather, ...suggestion } };
}

app.get('/advice', async (req, res) => {
  const result = await getAdvice(String(req.query.zip || ''));
  res.status(result.status).json(result.body);
});

app.get('/', async (req, res) => {
  const zip = String(req.query.zip || '');
  let resultHtml = '';

  if (zip) {
    const result = await getAdvice(zip);
    if (result.status === 200) {
      const body = result.body;
      resultHtml = `
        <h2>${escapeHtml(body.city)}, ${escapeHtml(body.state)}</h2>
        <p>${escapeHtml(body.forecast.temperature)}
          ${escapeHtml(body.forecast.temperatureUnit)} —
          ${escapeHtml(body.forecast.shortForecast)}</p>
        <p>${escapeHtml(body.advice)}</p>
        <p>Source: <strong>${escapeHtml(body.source)}</strong></p>
        <p>Cache: ${escapeHtml(body.cache)}</p>`;
    } else {
      resultHtml = `<p>Unable to provide advice:
        ${escapeHtml(result.body.error)}</p>`;
    }
  }

  // Always return 200: weather failures must not fail advisor health checks.
  res.status(200).type('html').send(`<!doctype html>
    <html lang="en">
    <head>
      <meta charset="utf-8">
      <meta name="viewport" content="width=device-width, initial-scale=1">
      <title>Weather Advisor</title>
      <style>
        body { font-family: Arial, sans-serif; max-width: 640px;
          margin: 60px auto; padding: 0 20px; line-height: 1.6; }
        input, button { padding: 10px; font-size: 16px; }
      </style>
    </head>
    <body>
      <h1>Weather Advisor</h1>
      <p>Enter a US ZIP code for a forecast and clothing suggestion.</p>
      <form method="get" action="/">
        <label for="zip">ZIP code</label>
        <input id="zip" name="zip" pattern="[0-9]{5}" maxlength="5"
          value="${escapeHtml(zip)}" required>
        <button type="submit">Get advice</button>
      </form>
      ${resultHtml}
    </body>
    </html>`);
});

app.listen(PORT, () => log('advisor_started', { port: PORT }));
