/**
 * Screenshots for the Projects (thin Project bundles) page.
 *
 * Drives the isolated capture entry (website/capture/project-bundles.html),
 * which mounts the REAL ProjectBundlesPage. Every REAL /api call is answered
 * by page.route on the pathname; the Project payloads MATCH the backend
 * (src/kiro_crew/dashboard/handlers_project.py `_project_payload`) field for
 * field. Every frame ASSERTS its state before writing, so a frame cannot
 * document the wrong state:
 *   fe-01-empty       no Projects -> the empty-state card
 *   fe-02-list        two healthy Projects (repo-backed + source-less)
 *   fe-03-detail      payments-platform detail: repo, Declared context, sessions
 *   fe-04-review      review_stale: Review-needed badge, stale files, disabled start,
 *                     the "Review files" button that opens the dialog with
 *                     "Sync project" beside it
 *   fe-05-unavailable sources_unavailable: badge, the action-first notice with the
 *                     manifest path on its own line, the missing source id in the
 *                     notice AND marked Unavailable inside the Repositories card
 *   fe-06-list-light  the two-Project list in light theme
 *   fe-07-review-dialog the digest-bound review dialog: each file's path, status
 *                     and whole content, a removed entry, two unreadable entries
 *                     (a link out of the tree, a file that is not text) each with
 *                     its path and a copy button, withholding "Accept these changes"
 *   fe-08-review-dialog-clean the same dialog with only acceptable entries, so
 *                     the primary action is live
 *
 * Usage:
 *   npx vite --host 127.0.0.1 --port 6832 --strictPort   # in another shell
 *   node scripts/capture-project-bundles.mjs http://127.0.0.1:6832 <evidence dir>
 */
import { chromium } from 'playwright'
import { mkdirSync } from 'node:fs'

const BASE = process.argv[2] || 'http://127.0.0.1:6832'
const OUT = process.argv[3] || '../temp-screenshots/project-bundles'
mkdirSync(OUT, { recursive: true })

// ── Fixtures: shaped exactly like `_project_payload` output ──────────────────
// A repo-backed, healthy Project. `workspace_source` names the primary source
// id (the checkout that supplies the working dir), matching the backend.
const PAYMENTS_ID = '5f2c9a71-8e0d-4b3a-9c14-7d6e2f0a1b33'
const PRIMARY_SOURCE_ID = 'payments-api-1a2b3c4d'
const INFRA_SOURCE_ID = 'payments-infra-3f9a1c2b'

const paymentsSources = [
  { id: PRIMARY_SOURCE_ID, type: 'repo', url: 'https://github.com/acme/payments-api', default_branch: 'main', role: 'primary' },
]
// A secondary reference repo that fails to clone in fe-05: the notice names its
// id and the Repositories card row carries the Unavailable badge.
const paymentsSourcesWithInfra = [
  ...paymentsSources,
  { id: INFRA_SOURCE_ID, type: 'repo', url: 'https://github.com/acme/payments-infra', default_branch: 'main', role: 'reference' },
]
const paymentsMcp = [{ name: 'atlassian', scope: {} }]
const paymentsRegistrations = [
  { origin: 'managed_git', path: '~/.kiro/crew/projects/managed/' + PAYMENTS_ID + '/bundle', syncable: true },
]
const paymentsSessions = [
  { key: 'sess-onboard-4821', title: 'Add idempotency keys to charge intents', messages: 34, running: false, live: false },
  { key: 'sess-refund-1190', title: 'Refund webhook replay audit', messages: 12, running: true, live: true },
]

function payments(health, sources = paymentsSources) {
  return {
    id: PAYMENTS_ID,
    name: 'payments-platform',
    description: 'Charge, refund and webhook services for the payments platform.',
    workspace_source: PRIMARY_SOURCE_ID,
    sources,
    mcp: paymentsMcp,
    memory: { mode: 'project' },
    registrations: paymentsRegistrations,
    health,
    sessions: paymentsSessions,
  }
}

const docsSite = {
  id: 'a1d47e90-3c22-49f5-8b6e-0f9c1a2b4d55',
  name: 'docs-site',
  description: 'Public documentation site.',
  workspace_source: 'self',
  sources: [],
  mcp: [],
  memory: { mode: 'none' },
  registrations: [{ origin: 'local', path: '~/projects/docs-site', syncable: false }],
  health: { status: 'healthy', code: 'project_healthy' },
  sessions: [],
}

const HEALTHY = { status: 'healthy', code: 'project_healthy' }
const STALE_FILES = ['.kiro/settings/mcp.json', '.kiro/agents/payments.md', '.kiro/hooks/legacy.json', '.kiro/skills/deploy/run.sh', '.kiro/skills/deploy/assets/badge.png']
const REVIEW_STALE = { status: 'review_stale', code: 'project_review_stale', stale_files: STALE_FILES }

// GET /api/project-bundles/{id}/review — shaped like the digest-bound preview
// contract: digest + files[{path, status}] plus the WHOLE `content` for
// added/changed and a `reason` for unreadable entries. There is no partial
// display: an entry is shown whole or is unreadable.
const MCP_JSON = [
  '{',
  '  "mcpServers": {',
  '    "atlassian": {',
  '      "command": "npx",',
  '      "args": ["-y", "mcp-atlassian"],',
  '      "env": { "ATLASSIAN_SITE": "acme" }',
  '    }',
  '  }',
  '}',
  '',
].join('\n')
const AGENT_MD = [
  '# payments agent',
  '',
  'Reconciles refund webhooks against charge intents and drafts the daily ledger note.',
  '',
  '## Tools',
  '- atlassian (PAY board)',
  '',
].join('\n')
const REVIEW_PREVIEW_BLOCKED = {
  digest: 'sha256:9c1f0a7d2e4b8f6a',
  files: [
    { path: '.kiro/settings/mcp.json', status: 'changed', content: MCP_JSON },
    { path: '.kiro/agents/payments.md', status: 'added', content: AGENT_MD },
    { path: '.kiro/hooks/legacy.json', status: 'removed' },
    { path: '.kiro/skills/deploy/run.sh', status: 'unreadable', reason: 'link-outside-root' },
    { path: '.kiro/skills/deploy/assets/badge.png', status: 'unreadable', reason: 'binary' },
  ],
}
const REVIEW_PREVIEW_CLEAN = {
  digest: 'sha256:5e2a9b0c7d1f3e8a',
  files: REVIEW_PREVIEW_BLOCKED.files.slice(0, 3),
}
const SOURCES_UNAVAILABLE = { status: 'sources_unavailable', code: 'project_sources_unavailable', unavailable_sources: [INFRA_SOURCE_ID] }

const browser = await chromium.launch()
let failed = false

function check(name, ok, detail) {
  console.log(`${name}: ${ok ? 'OK' : 'MISMATCH'} ${detail}`)
  if (!ok) failed = true
  return ok
}

/** Open the page with the given Project list and initial route/theme.
 *  Gateway-free: answer every REAL /api call. Predicate on the pathname so a
 *  glob does not swallow vite-served source modules. Array-shaped endpoints
 *  answer [] ({} crashes their .map consumers). */
async function open(projects, { theme = 'dark', route = '/project-bundles', reviewPreview = null } = {}) {
  const page = await browser.newPage({ viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1 })
  await page.route(u => new URL(u).pathname.startsWith('/api/'), route2 => {
    const path = new URL(route2.request().url()).pathname
    if (path === '/api/project-bundles') {
      return route2.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify({ projects }) })
    }
    if (reviewPreview && path === `/api/project-bundles/${PAYMENTS_ID}/review` && route2.request().method() === 'GET') {
      return route2.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(reviewPreview) })
    }
    const isList = /commands|skills|agents|sessions|files|history|models|artifacts|folders|slots$/.test(path)
    return route2.fulfill({ status: 200, contentType: 'application/json', body: isList ? '[]' : '{}' })
  })
  const url = `${BASE}/capture/project-bundles.html?theme=${theme}&route=${encodeURIComponent(route)}`
  await page.goto(url)
  await page.waitForSelector('[data-capture-root]')
  return page
}

// fe-01 — empty list
{
  const page = await open([])
  await page.getByTestId('project-bundles-empty').waitFor()
  const rows = await page.locator('[data-project-id]').count()
  check('fe-01 empty no rows', rows === 0, `rows=${rows}`)
  const empty = await page.getByTestId('project-bundles-empty').isVisible()
  check('fe-01 empty state visible', empty, 'empty-state card shown')
  const addLabel = await page.getByRole('button', { name: 'Add existing project' }).isVisible()
  check('fe-01 add button label', addLabel, 'header action reads "Add existing project"')
  await page.screenshot({ path: `${OUT}/fe-01-empty.png` })
  await page.close()
}

// fe-02 — two healthy Projects
{
  const page = await open([payments(HEALTHY), docsSite])
  await page.locator('[data-project-id]').first().waitFor()
  const rows = await page.locator('[data-project-id]').count()
  check('fe-02 two rows', rows === 2, `rows=${rows}`)
  const healthyBadges = await page.getByText('Healthy', { exact: true }).count()
  check('fe-02 both healthy', healthyBadges === 2, `healthy-badges=${healthyBadges}`)
  const names = (await page.locator('[data-project-id]').allTextContents()).join(' | ')
  check('fe-02 names present', /payments-platform/.test(names) && /docs-site/.test(names), 'both project names rendered')
  const addLabel = await page.getByRole('button', { name: 'Add existing project' }).isVisible()
  check('fe-02 add button label', addLabel, 'header action reads "Add existing project"')
  await page.screenshot({ path: `${OUT}/fe-02-list.png` })
  await page.close()
}

// fe-03 — payments-platform detail (healthy)
{
  const page = await open([payments(HEALTHY)], { route: `/project-bundles?project=${PAYMENTS_ID}` })
  await page.getByRole('heading', { name: 'payments-platform' }).waitFor()
  const headerHealthy = await page.getByText('Healthy', { exact: true }).count()
  check('fe-03 header healthy badge', headerHealthy >= 1, `healthy=${headerHealthy}`)
  const declared = await page.getByText('Declared context', { exact: true }).isVisible()
  check('fe-03 declared context card', declared, 'Declared context present')
  const pill = await page.getByText(/Declared — not yet active/).count()
  check('fe-03 no second-status pill', pill === 0, `"Declared — not yet active" count=${pill}`)
  const repoUrl = await page.getByText('https://github.com/acme/payments-api').isVisible()
  check('fe-03 repository url', repoUrl, 'primary repo url shown')
  const s1 = await page.getByText('Add idempotency keys to charge intents').isVisible()
  const s2 = await page.getByText('Refund webhook replay audit').isVisible()
  check('fe-03 two sessions listed', s1 && s2, 'both session titles rendered')
  const counted = await page.getByText('34 messages', { exact: true }).isVisible()
  check('fe-03 session row says what it counts', counted, '"34 messages", not a bare number')
  const startDisabled = await page.getByRole('button', { name: 'New session' }).isDisabled()
  check('fe-03 new-session enabled (healthy)', startDisabled === false, `disabled=${startDisabled}`)
  await page.screenshot({ path: `${OUT}/fe-03-detail.png` })
  await page.close()
}

// fe-04 — review_stale
{
  const page = await open([payments(REVIEW_STALE)], { route: `/project-bundles?project=${PAYMENTS_ID}` })
  await page.getByRole('heading', { name: 'payments-platform' }).waitFor()
  const badge = await page.getByText('Review needed', { exact: true }).isVisible()
  check('fe-04 review-needed badge', badge, 'Review needed badge shown')
  let listed = 0
  for (const file of STALE_FILES) if (await page.getByText(file, { exact: true }).isVisible()) listed += 1
  check('fe-04 stale files listed', listed === STALE_FILES.length, `listed=${listed}/${STALE_FILES.length} (any .kiro/ depth)`)
  const reviewBtn = page.getByRole('button', { name: 'Review files' })
  check('fe-04 review button', await reviewBtn.isVisible(), 'Review files button visible; accepting is not one click')
  // "Sync project" stands in the same action row as "Review files": every
  // unreadable remedy ends in "sync", so the sync is where the copy points.
  const actionRow = reviewBtn.locator('xpath=..')
  const syncBeside = await actionRow.getByRole('button', { name: 'Sync project' }).count()
  check('fe-04 sync beside review', syncBeside === 1, `Sync project in the Review files row (count=${syncBeside})`)
  const startDisabled = await page.getByRole('button', { name: 'New session' }).isDisabled()
  check('fe-04 new-session disabled', startDisabled === true, `disabled=${startDisabled}`)
  await page.screenshot({ path: `${OUT}/fe-04-review.png` })
  await page.close()
}

// fe-05 — sources_unavailable
{
  const page = await open([payments(SOURCES_UNAVAILABLE, paymentsSourcesWithInfra)], { route: `/project-bundles?project=${PAYMENTS_ID}` })
  await page.getByRole('heading', { name: 'payments-platform' }).waitFor()
  const badge = await page.getByText('Source unavailable', { exact: true }).isVisible()
  check('fe-05 source-unavailable badge', badge, 'Source unavailable badge shown')
  const idInNotice = await page.locator('li', { hasText: INFRA_SOURCE_ID }).count()
  check('fe-05 missing source id listed', idInNotice === 1, `notice lists ${INFRA_SOURCE_ID} (count=${idInNotice})`)
  const notice = await page.getByRole('alert').first().textContent()
  check('fe-05 notice leads with the action', /^Fix the source in the manifest, then sync\./.test((notice ?? '').trim()), 'first sentence is the fix')
  check('fe-05 manifest path on its own line', /\nManifest: ~\/\.kiro\/crew\/projects\/managed\/[^\n]*\/project\.yaml\n/.test(notice ?? ''), 'project.yaml path is a line of its own')
  const infraRow = page.locator('div.rounded-md', { hasText: 'https://github.com/acme/payments-infra' })
  const idInRow = await infraRow.getByText(INFRA_SOURCE_ID).count()
  check('fe-05 source id in the repositories card', idInRow >= 1, `repositories row names ${INFRA_SOURCE_ID}`)
  const rowBadge = await infraRow.getByText('Unavailable', { exact: true }).count()
  check('fe-05 unavailable badge on the repo row', rowBadge === 1, `Unavailable badge inside the infra row (count=${rowBadge})`)
  const apiRow = page.locator('div.rounded-md', { hasText: 'https://github.com/acme/payments-api' })
  const apiBadge = await apiRow.getByText('Unavailable', { exact: true }).count()
  check('fe-05 healthy repo row unmarked', apiBadge === 0, `primary row carries no Unavailable badge (count=${apiBadge})`)
  const startDisabled = await page.getByRole('button', { name: 'New session' }).isDisabled()
  check('fe-05 new-session disabled', startDisabled === true, `disabled=${startDisabled}`)
  // Taller frame: the banner naming the id and the Repositories row wearing
  // the badge must both be in the same picture.
  await page.setViewportSize({ width: 1440, height: 1240 })
  await page.screenshot({ path: `${OUT}/fe-05-unavailable.png` })
  await page.close()
}

// fe-06 — the two-Project list in light theme
{
  const page = await open([payments(HEALTHY), docsSite], { theme: 'light' })
  await page.locator('[data-project-id]').first().waitFor()
  const rows = await page.locator('[data-project-id]').count()
  check('fe-06 two rows (light)', rows === 2, `rows=${rows}`)
  const themeAttr = await page.evaluate(() => document.documentElement.getAttribute('data-theme'))
  check('fe-06 light theme', themeAttr === 'kiro-light', `data-theme=${themeAttr}`)
  const addLabel = await page.getByRole('button', { name: 'Add existing project' }).isVisible()
  check('fe-06 add button label', addLabel, 'header action reads "Add existing project"')
  await page.screenshot({ path: `${OUT}/fe-06-list-light.png` })
  await page.close()
}

/** Open the review-stale detail and click through to the review dialog. */
async function openReviewDialog(reviewPreview) {
  const page = await open([payments(REVIEW_STALE)], { route: `/project-bundles?project=${PAYMENTS_ID}`, reviewPreview })
  await page.getByRole('heading', { name: 'payments-platform' }).waitFor()
  await page.getByRole('button', { name: 'Review files' }).click()
  const dialog = page.getByRole('dialog')
  await dialog.waitFor()
  await dialog.getByTestId('project-review-file').first().waitFor()
  return { page, dialog }
}

// fe-07 — the review dialog with never-acceptable entries
{
  const { page, dialog } = await openReviewDialog(REVIEW_PREVIEW_BLOCKED)
  const title = await dialog.getByText('Review changed files in payments-platform').isVisible()
  check('fe-07 dialog title', title, 'changed-files title (not the first-review one)')
  const rows = await dialog.getByTestId('project-review-file').count()
  check('fe-07 five files', rows === 5, `rows=${rows}`)
  const mcpContent = await dialog.getByLabel('Content of .kiro/settings/mcp.json').textContent()
  check('fe-07 mcp.json content shown whole', mcpContent === MCP_JSON, 'the bytes being accepted are on screen, all of them')
  const agentContent = await dialog.getByLabel('Content of .kiro/agents/payments.md').textContent()
  check('fe-07 agent content shown whole', agentContent === AGENT_MD, 'markdown agent body on screen, all of it')
  const partial = await dialog.getByText(/Only the first part of this file/).count()
  check('fe-07 no partial-display note', partial === 0, `partial-note count=${partial}`)
  const removed = await dialog.getByText('This file was removed. Accepting records that it is gone.').isVisible()
  check('fe-07 removed entry explained', removed, 'removed entry has no content and says why')
  const link = await dialog.getByText(/link that points outside the Project/).isVisible()
  check('fe-07 link entry explained', link, 'link-outside-root reason rendered')
  const binary = await dialog.getByText(/This file is not text, so its content cannot be shown or accepted\. Replace it with a text file inside the Project, sync, and review again\./).isVisible()
  check('fe-07 binary entry explained', binary, 'binary reason rendered, ending in sync-and-review')
  // Each unreadable entry hands over its path: a line of its own plus a
  // labelled copy button, so the remedy is actionable.
  const fixPaths = dialog.getByTestId('project-review-fix-path')
  const fixCount = await fixPaths.count()
  check('fe-07 fix-path lines', fixCount === 2, `fix-path lines=${fixCount}`)
  const fixTexts = await fixPaths.allTextContents()
  check('fe-07 fix-path names both files', /\.kiro\/skills\/deploy\/run\.sh/.test(fixTexts[0] ?? '') && /badge\.png/.test(fixTexts[1] ?? ''), `paths=${JSON.stringify(fixTexts)}`)
  const copyButtons = await dialog.getByRole('button', { name: 'Copy path' }).count()
  check('fe-07 copy-path buttons', copyButtons === 2, `Copy path buttons=${copyButtons}`)
  const blockedNote = await dialog.getByTestId('project-review-blocked').textContent()
  check('fe-07 blocked note', /2 entries cannot be accepted until they are fixed/.test(blockedNote ?? ''), `note=${JSON.stringify(blockedNote)}`)
  const acceptDisabled = await dialog.getByRole('button', { name: 'Accept these changes' }).isDisabled()
  check('fe-07 accept withheld', acceptDisabled === true, `disabled=${acceptDisabled}`)
  const statuses = await dialog.getByTestId('project-review-file').evaluateAll(rows =>
    rows.map(row => row.querySelector('span.rounded-full')?.textContent))
  check('fe-07 status badges', JSON.stringify(statuses) === JSON.stringify(['Changed', 'Added', 'Removed', 'Unreadable', 'Unreadable']), `statuses=${JSON.stringify(statuses)}`)
  // The frame documents the withheld accept: scroll the body so both
  // unreadable entries and the blocked note sit above the disabled button.
  await dialog.getByTestId('project-review-blocked').scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${OUT}/fe-07-review-dialog.png` })
  await page.close()
}

// fe-08 — the review dialog with only acceptable entries
{
  const { page, dialog } = await openReviewDialog(REVIEW_PREVIEW_CLEAN)
  const rows = await dialog.getByTestId('project-review-file').count()
  check('fe-08 three files', rows === 3, `rows=${rows}`)
  const blocked = await dialog.getByTestId('project-review-blocked').count()
  check('fe-08 no blocked note', blocked === 0, `blocked-note count=${blocked}`)
  const acceptDisabled = await dialog.getByRole('button', { name: 'Accept these changes' }).isDisabled()
  check('fe-08 accept live', acceptDisabled === false, `disabled=${acceptDisabled}`)
  await page.screenshot({ path: `${OUT}/fe-08-review-dialog-clean.png` })
  await page.close()
}

await browser.close()
if (failed) {
  console.error('CAPTURE FAILED: at least one frame did not match its asserted state')
  process.exit(1)
}
console.log('all frames verified')
