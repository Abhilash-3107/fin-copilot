// A deterministic stand-in for the model providers the app talks to.
//
// Speaks the Ollama endpoints the app calls (/api/chat, /api/embed, /api/tags,
// /api/ps) and the OpenAI-compatible /v1/chat/completions and
// /v1/models. Chat answers come from a small keyword table keyed on the
// transaction description, so every AI path gives the same answer on every run.
// Embeddings are hashed character trigrams, L2-normalised: the same text gives
// the same vector and similar text gives similar vectors.
//
// The app asks for one transaction per chat call and only embeds through
// Ollama, so there is no batched chat shape and no /v1/embeddings here.
import http from 'node:http'

export const CHAT_MODEL = 'qwen3.5:4b'
export const EMBED_MODEL = 'nomic-embed-text'

// [pattern, category, subcategory, merchant, confidence]
const KEYWORDS = [
  [/SWIGGY|ZOMATO|DUNZO|LICIOUS/i, 'Food & Dining', 'Food Delivery'],
  [/BIGBASKET|ZEPTO|BLINKIT/i, 'Food & Dining', 'Groceries'],
  [/COFFEE|CAFE|KAFE|BAKEHOUSE|CHAI|TOKAI/i, 'Food & Dining', 'Cafe & Snacks'],
  [/BEACH SHACK|RESTAURANT/i, 'Food & Dining', 'Restaurants'],
  [/UBER|OLA\b|RAPIDO/i, 'Transport', 'Cab & Auto'],
  [/METRO|BMRCL/i, 'Transport', 'Public Transport'],
  [/FUEL|PETROL|HPCL|IOCL/i, 'Transport', 'Fuel'],
  [/AMAZON|FLIPKART|MYNTRA/i, 'Shopping', 'Online Shopping'],
  [/CROMA|DECATHLON/i, 'Shopping', 'General Retail'],
  [/NETFLIX|SPOTIFY|HOTSTAR/i, 'Entertainment', 'Movies & OTT'],
  [/BESCOM|ELECTRICITY/i, 'Bills & Utilities', 'Electricity'],
  [/ACT FIBERNET|BROADBAND/i, 'Bills & Utilities', 'Internet & Broadband'],
  [/JIO|AIRTEL|RECHARGE/i, 'Bills & Utilities', 'Mobile Recharge'],
  [/PRESTIGE|SOCIETY|MAINTENANCE/i, 'Housing', 'Maintenance & Society Charges'],
  [/RENT/i, 'Housing', 'Rent'],
  [/APOLLO|PHARMEASY|PHARMACY/i, 'Health', 'Pharmacy'],
  [/PRACTO|DOCTOR|HOSPITAL/i, 'Health', 'Doctor & Hospital'],
  [/CULT\.?FIT|GYM/i, 'Health', 'Gym & Fitness'],
  [/NATURALS|SALON/i, 'Personal Care', null],
  [/GROWW|ZERODHA|SIP\b/i, 'Investments', 'Mutual Fund SIP'],
  [/SAL CREDIT|SALARY/i, 'Income', 'Salary'],
  [/UDEMY|COURSE/i, 'Education', 'Online Courses'],
  [/IRCTC|MAKEMYTRIP|IXIGO/i, 'Travel', null],
  [/KETTO|DONATION/i, 'Gifts & Donations', 'Charity'],
]

const HIT_CONFIDENCE = 0.95
const MISS_CONFIDENCE = 0.3

function lastMatch(text, re) {
  const all = [...text.matchAll(re)]
  return all.length ? all[all.length - 1][1].trim() : ''
}

// The transaction being classified is the unindented "Description:" line; the
// few-shot examples before it indent theirs.
export function answerFor(prompt, sureOfUncategorized = []) {
  const description = lastMatch(prompt, /^Description: (.*)$/gm)
  // A model that is confident a payment has no category: the answer a person
  // would call "not sorted" however sure the model sounds.
  if (sureOfUncategorized.some((re) => re.test(description))) {
    return { category: 'Uncategorized', subcategory: null, merchant: null, tags: [], confidence: 1 }
  }
  const offered = [...prompt.matchAll(/^ {2}- (.+)$/gm)].map((m) => m[1].trim())
  const tops = new Set(offered.map((c) => c.split(' > ')[0]))
  const hit = KEYWORDS.find(([re]) => re.test(description))
  if (hit && tops.has(hit[1])) {
    const [, category, sub] = hit
    const subcategory = sub && offered.includes(`${category} > ${sub}`) ? sub : null
    return { category, subcategory, merchant: null, tags: [], confidence: HIT_CONFIDENCE }
  }
  const category = tops.has('Miscellaneous') ? 'Miscellaneous' : 'Uncategorized'
  return { category, subcategory: null, merchant: null, tags: [], confidence: MISS_CONFIDENCE }
}

function fnv1a(str) {
  let h = 0x811c9dc5
  for (let i = 0; i < str.length; i++) {
    h ^= str.charCodeAt(i)
    h = Math.imul(h, 0x01000193) >>> 0
  }
  return h
}

export function embeddingDim(model) {
  return /nomic/i.test(model || EMBED_MODEL) ? 768 : 384
}

export function embed(text, dim) {
  const vec = new Array(dim).fill(0)
  const padded = `  ${String(text).toLowerCase()}  `
  for (let i = 0; i + 3 <= padded.length; i++) vec[fnv1a(padded.slice(i, i + 3)) % dim] += 1
  const norm = Math.sqrt(vec.reduce((s, x) => s + x * x, 0)) || 1
  return vec.map((x) => x / norm)
}

function readJson(req) {
  return new Promise((resolve, reject) => {
    const chunks = []
    req.on('data', (c) => chunks.push(c))
    req.on('end', () => {
      const raw = Buffer.concat(chunks).toString()
      try {
        resolve(raw ? JSON.parse(raw) : {})
      } catch (e) {
        reject(e)
      }
    })
    req.on('error', reject)
  })
}

function lastUserPrompt(messages = []) {
  const users = messages.filter((m) => m.role === 'user')
  // A self-repair re-ask ends with a user nudge; the prompt is the first user turn.
  return users.length ? users[0].content : ''
}

/** Start the stub on a free loopback port. */
export async function startStubModel() {
  const requests = []
  // While held, chat answers wait: a test can keep one sorting job running and
  // watch the statements queued behind it, then let them all through.
  let gate = null
  let openGate = () => {}
  const sureOfUncategorized = []
  const server = http.createServer(async (req, res) => {
    const path = new URL(req.url, 'http://stub').pathname
    let body = {}
    if (req.method === 'POST') {
      try {
        body = await readJson(req)
      } catch {
        res.writeHead(400).end('{"error":"bad json"}')
        return
      }
    }
    requests.push({ method: req.method, path, body })
    const send = (obj, status = 200) => {
      res.writeHead(status, { 'content-type': 'application/json' })
      res.end(JSON.stringify(obj))
    }

    if (req.method === 'GET' && path === '/api/tags') {
      return send({ models: [{ name: CHAT_MODEL }, { name: `${EMBED_MODEL}:latest` }] })
    }
    if (req.method === 'GET' && path === '/api/ps') {
      return send({ models: [{ name: CHAT_MODEL }] })
    }
    if (req.method === 'GET' && path === '/v1/models') {
      return send({ data: [{ id: CHAT_MODEL }] })
    }
    if (req.method === 'POST' && (path === '/api/chat' || path === '/v1/chat/completions')) await gate
    if (req.method === 'POST' && path === '/api/chat') {
      const content = JSON.stringify(answerFor(lastUserPrompt(body.messages), sureOfUncategorized))
      return send({ model: body.model, message: { role: 'assistant', content }, done: true })
    }
    if (req.method === 'POST' && path === '/v1/chat/completions') {
      const content = JSON.stringify(answerFor(lastUserPrompt(body.messages), sureOfUncategorized))
      return send({ model: body.model, choices: [{ message: { role: 'assistant', content } }] })
    }
    if (req.method === 'POST' && path === '/api/embed') {
      const inputs = Array.isArray(body.input) ? body.input : [body.input]
      const dim = embeddingDim(body.model)
      return send({ model: body.model, embeddings: inputs.map((t) => embed(t, dim)) })
    }
    return send({ error: `stub: no route for ${req.method} ${path}` }, 404)
  })

  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve))
  const { port } = server.address()
  return {
    url: `http://127.0.0.1:${port}`,
    port,
    requests,
    /** From now on answer "Uncategorized, sure of it" for descriptions matching `pattern`. */
    answerUncategorized: (pattern) => { sureOfUncategorized.push(pattern) },
    /** Make chat answers wait until release(). */
    hold: () => {
      if (!gate) gate = new Promise((resolve) => { openGate = resolve })
    },
    release: () => {
      openGate()
      gate = null
    },
    close: () => new Promise((resolve) => server.close(resolve)),
  }
}
