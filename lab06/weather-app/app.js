const fs = require('fs');
const express = require('express');
const { Pool } = require('pg');
const { SSMClient, GetParameterCommand } = require('@aws-sdk/client-ssm');

const app = express();
const PORT = Number(process.env.PORT || 3000);
const ZIP_API = process.env.ZIP_API_BASE || 'https://api.zippopotam.us';
const NWS_API = process.env.NWS_API_BASE || 'https://api.weather.gov';
const USER_AGENT = process.env.NWS_USER_AGENT;
const CACHE_SECONDS = Number(process.env.CACHE_SECONDS || 600);
const ssm = new SSMClient({ region: process.env.AWS_REGION });
const log = (event, fields = {}) =>
  console.log(JSON.stringify({ ts: new Date().toISOString(), event, ...fields }));

class UpstreamError extends Error {
  constructor(stage, status) {
    super(stage);
    this.stage = stage;
    this.status = status;
  }
}

async function fetchJson(stage, url, headers = {}) {
  let status = 0;
  for (let attempt = 0; attempt < 2; attempt++) {
    try {
      const response = await fetch(url, {
        headers, signal: AbortSignal.timeout(8000)
      });
      status = response.status;
      if (response.ok) return await response.json();
      if (status === 404) return null;
      if (status < 500 && status !== 429) break;
    } catch {
      status = 0;
    }
    if (attempt === 0) {
      await new Promise(resolve => setTimeout(resolve, 500));
    }
  }
  throw new UpstreamError(stage, status);
}

let poolPromise = null;
let dbRetryAt = 0;

async function createPool() {
  const result = await ssm.send(new GetParameterCommand({
    Name: process.env.DB_PASSWORD_PARAM,
    WithDecryption: true
  }), { abortSignal: AbortSignal.timeout(5000) });

  const pool = new Pool({
    host: process.env.DB_HOST,
    port: Number(process.env.DB_PORT || 5432),
    database: process.env.DB_NAME,
    user: process.env.DB_USER,
    password: result.Parameter.Value,
    ssl: {
      ca: fs.readFileSync(
        process.env.DB_CA_FILE || '/opt/app/rds-ca.pem', 'utf8'
      ),
      rejectUnauthorized: true
    },
    max: 4,
    connectionTimeoutMillis: 3000,
    idleTimeoutMillis: 30000,
    statement_timeout: 3000
  });
  pool.on('error', err => log('db_error', {
    stage: 'pool', code: err.code || err.name
  }));
  try {
    await pool.query(`CREATE TABLE IF NOT EXISTS lookups (
      id BIGSERIAL PRIMARY KEY,
      zip CHAR(5) NOT NULL,
      payload JSONB NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now()
    )`);
    await pool.query(
      'CREATE INDEX IF NOT EXISTS lookups_zip_created ' +
      'ON lookups (zip, created_at DESC)'
    );
  } catch (err) {
    await pool.end().catch(() => {});
    throw err;
  }
  return pool;
}

function getPool() {
  if (!poolPromise) {
    poolPromise = createPool().catch(err => {
      poolPromise = null;
      throw err;
    });
  }
  return poolPromise;
}

async function withDb(stage, operation) {
  if (!process.env.DB_HOST) return { state: 'disabled' };
  if (Date.now() < dbRetryAt) return { state: 'unavailable' };
  try {
    return { state: 'ok', value: await operation(await getPool()) };
  } catch (err) {
    dbRetryAt = Date.now() + 30000;
    log('db_error', { stage, code: err.code || err.name });
    return { state: 'unavailable' };
  }
}

app.get('/', (req, res) => {
  res.json({
    service: 'weather',
    usage: '/weather?zip=60616',
    history: '/history'
  });
});

app.get('/history', async (req, res) => {
  const result = await withDb('history', async pool => {
    const query = await pool.query(`
      SELECT id, zip, payload->>'city' AS city,
        payload->>'state' AS state,
        payload->'forecast'->>'shortForecast' AS short_forecast,
        payload->'forecast'->>'temperature' AS temperature,
        created_at
      FROM lookups ORDER BY created_at DESC LIMIT 10
    `);
    return query.rows;
  });
  if (result.state !== 'ok') {
    return res.status(503).json({
      error: `database_${result.state}`
    });
  }
  res.json({ count: result.value.length, lookups: result.value });
});

app.get('/weather', async (req, res) => {
  const zip = String(req.query.zip || '');
  if (!/^\d{5}$/.test(zip)) {
    log('weather_rejected', { reason: 'invalid_zip' });
    return res.status(400).json({ error: 'invalid_zip' });
  }

  const started = Date.now();
  log('weather_request', { zip });
  const cached = await withDb('read', async pool => {
    const result = await pool.query(`
      SELECT payload FROM lookups
      WHERE zip = $1 AND created_at > now() - make_interval(secs => $2)
      ORDER BY created_at DESC LIMIT 1
    `, [zip, CACHE_SECONDS]);
    return result.rows[0]?.payload;
  });
  if (cached.state === 'ok' && cached.value) {
    log('weather_success', {
      zip, cache: 'hit', ms: Date.now() - started
    });
    return res.json({ ...cached.value, cache: 'hit' });
  }

  try {
    const lookup = await fetchJson('zip_lookup', `${ZIP_API}/us/${zip}`);
    if (!lookup?.places?.length) {
      return res.status(404).json({ error: 'zip_not_found' });
    }
    const place = lookup.places[0];
    const latitude = Number(place.latitude).toFixed(4);
    const longitude = Number(place.longitude).toFixed(4);
    const headers = {
      'User-Agent': USER_AGENT,
      Accept: 'application/geo+json'
    };
    const point = await fetchJson(
      'nws_points', `${NWS_API}/points/${latitude},${longitude}`, headers
    );
    if (!point) {
      return res.status(404).json({ error: 'no_nws_coverage' });
    }
    if (!point.properties?.forecast) {
      throw new UpstreamError('nws_points', 502);
    }
    const forecast = await fetchJson(
      'nws_forecast', point.properties.forecast, headers
    );
    const period = forecast?.properties?.periods?.[0];
    if (!period) throw new UpstreamError('nws_forecast', 502);

    const body = {
      zip,
      city: place['place name'],
      state: place['state abbreviation'],
      latitude,
      longitude,
      forecast: {
        name: period.name,
        temperature: period.temperature,
        temperatureUnit: period.temperatureUnit,
        shortForecast: period.shortForecast,
        windSpeed: period.windSpeed,
        precipitationChance: period.probabilityOfPrecipitation?.value ?? null,
        isDaytime: period.isDaytime
      }
    };
    const saved = await withDb('write', pool => pool.query(
      'INSERT INTO lookups (zip, payload) VALUES ($1, $2)',
      [zip, JSON.stringify(body)]
    ));
    const cache = saved.state === 'ok' ? 'miss' : saved.state;
    log('weather_success', { zip, cache, ms: Date.now() - started });
    res.json({ ...body, cache });
  } catch (err) {
    if (err instanceof UpstreamError) {
      log('weather_error', { zip, stage: err.stage, status: err.status });
      return res.status(502).json({
        error: 'upstream_error', stage: err.stage
      });
    }
    log('weather_error', { zip, stage: 'internal', code: err.name });
    res.status(500).json({ error: 'internal_error' });
  }
});

app.listen(PORT, () => log('weather_started', { port: PORT }));
