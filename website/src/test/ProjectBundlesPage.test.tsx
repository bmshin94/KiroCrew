import { beforeEach, describe, expect, it, vi } from 'vitest'
import { fireEvent, screen, waitFor, within } from '@testing-library/react'

import { api } from '../api/client'
import ProjectBundlesPage from '../pages/ProjectBundlesPage'
import { renderWithProviders } from './helpers'

vi.mock('../api/client', () => ({
  api: {
    projectBundles: vi.fn(),
    createProjectBundle: vi.fn(),
    addProjectBundle: vi.fn(),
    syncProjectBundle: vi.fn(),
    projectBundleReviewPreview: vi.fn(),
    reviewProjectBundle: vi.fn(),
    removeProjectBundle: vi.fn(),
    createChatSlot: vi.fn(),
    chatSlotProject: vi.fn(),
    setSlotColor: vi.fn(),
    setSlotColorHex: vi.fn(),
    deleteChatSlot: vi.fn(),
  },
}))

const localProject = {
  id: '018f4f4a-760f-7a8b-a5d4-5a7e0f130d4e',
  name: 'Payments Platform',
  description: 'Payments services and operational context.',
  workspace_source: 'payments-api',
  sources: [{
    id: 'payments-api',
    type: 'repo',
    url: 'https://github.com/acme/payments-api',
    default_branch: 'main',
  }],
  mcp: [
    { name: 'atlassian', scope: { site: 'acme', project: 'PAY' } },
    { name: 'datadog' },
  ],
  memory: { mode: 'project' as const },
  registrations: [{ origin: 'local' as const, path: '/work/payments', syncable: false }],
  health: { status: 'healthy' as const, code: 'project_healthy' },
  sessions: [{
    key: 'payments-chat',
    title: 'Investigate refunds',
    messages: 4,
    running: false,
    live: true,
  }],
}

const reviewPreview = {
  digest: 'sha256:0f1e2d3c',
  files: [
    {
      path: '.kiro/settings/mcp.json',
      status: 'changed' as const,
      content: '{\n  "mcpServers": {\n    "atlassian": { "command": "npx", "args": ["-y", "mcp-atlassian"] }\n  }\n}\n',
    },
    {
      path: '.kiro/agents/payments.md',
      status: 'added' as const,
      content: '# payments agent\nRuns the refund reconciliation.\n',
    },
    { path: '.kiro/hooks/legacy.json', status: 'removed' as const },
  ],
}

const managedProject = {
  ...localProject,
  id: '018f4f4a-760f-7a8b-a5d4-5a7e0f130d5f',
  name: 'Shared Payments',
  registrations: [{
    origin: 'managed_git' as const,
    path: '/data/projects/shared-payments',
    syncable: true,
  }],
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.projectBundles).mockResolvedValue({ projects: [localProject] })
  vi.mocked(api.createProjectBundle).mockResolvedValue(localProject)
  vi.mocked(api.addProjectBundle).mockResolvedValue(localProject)
  vi.mocked(api.syncProjectBundle).mockResolvedValue(managedProject)
  vi.mocked(api.projectBundleReviewPreview).mockResolvedValue(reviewPreview)
  vi.mocked(api.reviewProjectBundle).mockResolvedValue(localProject)
  vi.mocked(api.removeProjectBundle).mockResolvedValue({ ok: true, id: localProject.id })
  vi.mocked(api.createChatSlot).mockResolvedValue({
    key: 'new-project-chat',
    title: 'New Session',
    messages: 0,
    running: false,
    project: '/work/payments',
    project_id: localProject.id,
  })
})

describe('Projects portal (thin Project)', () => {
  it('opens a Project from a single-column list into a focused detail view', async () => {
    renderWithProviders(<ProjectBundlesPage />)

    const project = await screen.findByRole('button', { name: /Open project Payments Platform/ })
    expect(screen.queryByRole('button', { name: 'New session' })).not.toBeInTheDocument()

    fireEvent.click(project)

    expect(await screen.findByRole('heading', { name: 'Payments Platform' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Back to projects' })).toBeInTheDocument()
    expect(screen.getByText('Payments services and operational context.')).toBeInTheDocument()
    expect(screen.getAllByText('payments-api')).toHaveLength(2)
    expect(screen.getByText('https://github.com/acme/payments-api')).toBeInTheDocument()
    expect(screen.getByText('/work/payments')).toBeInTheDocument()
    expect(screen.getByText('Healthy')).toBeInTheDocument()
    // The thin Project installs nothing: no activation / capabilities control.
    expect(screen.queryByRole('button', { name: /Trust and activate/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Deactivate/ })).not.toBeInTheDocument()
    expect(screen.getByText('Investigate refunds')).toBeInTheDocument()
    // A session row says what it counts, not a bare number.
    expect(screen.getByText('4 messages')).toBeInTheDocument()
  })

  it('pluralizes a single message in a session row', async () => {
    vi.mocked(api.projectBundles).mockResolvedValue({
      projects: [{ ...localProject, sessions: [{ ...localProject.sessions[0], messages: 1 }] }],
    })
    renderWithProviders(<ProjectBundlesPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Open project Payments Platform/ }))

    expect(screen.getByText('1 message')).toBeInTheDocument()
  })

  it('lists declared MCP servers and project memory as not yet active', async () => {
    renderWithProviders(<ProjectBundlesPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Open project Payments Platform/ }))

    expect(screen.getByText('Declared context')).toBeInTheDocument()
    // One helper line names where these come from and that nothing acts on
    // them yet — in place of a badge that read as a second status.
    expect(screen.getByText('Listed in project.yaml. A later step turns these on for sessions.')).toBeInTheDocument()
    expect(screen.queryByText('Declared — not yet active')).not.toBeInTheDocument()
    expect(screen.getByText('atlassian')).toBeInTheDocument()
    expect(screen.getByText(/site: acme/)).toBeInTheDocument()
    expect(screen.getByText('datadog')).toBeInTheDocument()
    expect(screen.getByText(/This Project gets its own private memory/)).toBeInTheDocument()
  })

  it('renders only repository sources in detail when provider data contains objects', async () => {
    const projectWithExtensionSource = {
      ...localProject,
      sources: [
        ...localProject.sources,
        { id: 'pay-board', type: 'jira', url: { board: 'PAY' } },
      ],
    }
    vi.mocked(api.projectBundles).mockResolvedValue({ projects: [projectWithExtensionSource] })
    renderWithProviders(<ProjectBundlesPage />)

    fireEvent.click(await screen.findByRole('button', { name: /Open project Payments Platform/ }))

    expect(screen.getByText('https://github.com/acme/payments-api')).toBeInTheDocument()
    expect(screen.queryByText('pay-board')).not.toBeInTheDocument()
  })

  it('starts a session with the Project identity in the create request', async () => {
    renderWithProviders(<ProjectBundlesPage />)

    fireEvent.click(await screen.findByRole('button', { name: /Open project Payments Platform/ }))
    fireEvent.click(screen.getByRole('button', { name: 'New session' }))

    await waitFor(() => {
      expect(api.createChatSlot).toHaveBeenCalledWith(
        undefined,
        undefined,
        undefined,
        undefined,
        // createSlot resolves the configured default memory mode before the
        // request; this test pins the Project identity, not that default.
        expect.any(String),
        undefined,
        undefined,
        undefined,
        undefined,
        // adopt_remote_slot — unset here.
        undefined,
        localProject.id,
      )
    })
  })

  it('explains how to populate an empty registry', async () => {
    vi.mocked(api.projectBundles).mockResolvedValue({ projects: [] })

    renderWithProviders(<ProjectBundlesPage />)

    expect(await screen.findByText('No projects yet')).toBeInTheDocument()
    expect(screen.getByText('Create a local Project or add one from a folder or Git URL.')).toBeInTheDocument()
  })

  it('creates a local bundle and refreshes the portal list', async () => {
    vi.mocked(api.projectBundles)
      .mockResolvedValueOnce({ projects: [] })
      .mockResolvedValue({ projects: [localProject] })

    renderWithProviders(<ProjectBundlesPage />)
    await screen.findByText('No projects yet')
    fireEvent.click(screen.getByRole('button', { name: 'Create project' }))
    fireEvent.change(screen.getByLabelText('Project name'), {
      target: { value: 'Payments Platform' },
    })
    const projectFolder = screen.getByLabelText('Project folder')
    fireEvent.change(projectFolder, {
      target: { value: '/work/payments' },
    })
    fireEvent.click(within(projectFolder.closest('form')!).getByRole('button', { name: 'Create project' }))

    expect(await screen.findByRole('button', { name: /Open project Payments Platform/ })).toBeInTheDocument()
  })

  it('adds an existing folder or Git URL and refreshes the portal list', async () => {
    vi.mocked(api.projectBundles)
      .mockResolvedValueOnce({ projects: [] })
      .mockResolvedValue({ projects: [localProject] })

    renderWithProviders(<ProjectBundlesPage />)
    await screen.findByText('No projects yet')
    // "Add existing project" is distinguishable from "Create project" at a
    // glance; the helper text keeps the folder-or-URL detail.
    expect(screen.queryByRole('button', { name: 'Add project' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Add existing project' }))
    expect(screen.getByText('Register an existing Project folder, or clone one from a Git URL.')).toBeInTheDocument()
    const projectSource = screen.getByLabelText('Folder or Git URL')
    fireEvent.change(projectSource, {
      target: { value: '/work/payments' },
    })
    fireEvent.click(within(projectSource.closest('form')!).getByRole('button', { name: 'Add existing project' }))

    expect(await screen.findByRole('button', { name: /Open project Payments Platform/ })).toBeInTheDocument()
  })

  it('names the sandbox requirement when a host that denies user namespaces refuses an add', async () => {
    vi.mocked(api.projectBundles).mockResolvedValue({ projects: [] })
    vi.mocked(api.addProjectBundle).mockRejectedValue(Object.assign(new Error('sandbox unavailable'), {
      status: 503,
      body: JSON.stringify({ error: 'sandbox unavailable', code: 'project_sandbox_unavailable' }),
    }))

    renderWithProviders(<ProjectBundlesPage />)
    await screen.findByText('No projects yet')
    fireEvent.click(screen.getByRole('button', { name: 'Add existing project' }))
    const projectSource = screen.getByLabelText('Folder or Git URL')
    fireEvent.change(projectSource, { target: { value: 'https://github.com/acme/payments' } })
    fireEvent.click(within(projectSource.closest('form')!).getByRole('button', { name: 'Add existing project' }))

    // The notice repeats the requirement rather than echoing the server's
    // short reason, and offers the agent hand-off for a host-level fix.
    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Project Git operations run in the Kiro Crew sandbox.')
    expect(alert).toHaveTextContent('user namespaces are disabled')
    expect(within(alert).getByRole('button', { name: /Ask the agent/ })).toBeInTheDocument()
  })

  it('syncs managed Git projects and confirms completion', async () => {
    vi.mocked(api.projectBundles).mockResolvedValue({ projects: [managedProject] })

    renderWithProviders(<ProjectBundlesPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Open project Shared Payments/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Sync project' }))

    expect(await screen.findByText('Project synced.')).toBeInTheDocument()
  })

  it('offers recovery for an unavailable Git Project and explains why sessions are blocked', async () => {
    const unavailable = {
      ...managedProject,
      health: { status: 'unavailable' as const, code: 'project_manifest_unavailable' },
    }
    vi.mocked(api.projectBundles).mockResolvedValue({ projects: [unavailable] })

    renderWithProviders(<ProjectBundlesPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Open project Shared Payments/ }))

    expect(screen.getByRole('alert')).toHaveTextContent('Project files are unavailable')
    expect(screen.getByRole('button', { name: 'Retry sync' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'New session' })).toBeDisabled()
  })

  const reviewStale = {
    ...managedProject,
    health: {
      status: 'review_stale' as const,
      code: 'project_review_stale',
      stale_files: ['.kiro/settings/mcp.json', '.kiro/agents/payments.md', '.kiro/hooks/legacy.json'],
    },
  }

  async function openReviewDialog() {
    renderWithProviders(<ProjectBundlesPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Open project Shared Payments/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Review files' }))
    return within(await screen.findByRole('dialog'))
  }

  it('flags a review-stale Project and lists the files awaiting review as relative paths', async () => {
    vi.mocked(api.projectBundles).mockResolvedValue({ projects: [reviewStale] })

    renderWithProviders(<ProjectBundlesPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Open project Shared Payments/ }))

    // Warning badge, copy that reads for a first review as well as a changed
    // one, and every stale path rendered as received — any `.kiro/` depth.
    expect(screen.getByText('Review needed')).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('that can run code are waiting for your review')
    expect(screen.getByText('.kiro/settings/mcp.json')).toBeInTheDocument()
    expect(screen.getByText('.kiro/agents/payments.md')).toBeInTheDocument()
    expect(screen.getByText('.kiro/hooks/legacy.json')).toBeInTheDocument()
    // Sessions stay blocked until the owner accepts the changes; nothing was
    // accepted by merely opening the page.
    expect(screen.getByRole('button', { name: 'New session' })).toBeDisabled()
    expect(api.reviewProjectBundle).not.toHaveBeenCalled()
    // Every unreadable remedy ends in "sync", so Sync stands beside Review
    // files in this state and runs the page's one sync action.
    const actions = screen.getByRole('button', { name: 'Review files' }).parentElement!
    expect(within(actions).getByRole('button', { name: 'Sync project' })).toBeInTheDocument()
    fireEvent.click(within(actions).getByRole('button', { name: 'Sync project' }))
    await waitFor(() => expect(api.syncProjectBundle).toHaveBeenCalledWith(reviewStale.id))
  })

  it('withholds accept for a file that is not text, and hands over the path to fix', async () => {
    vi.mocked(api.projectBundles).mockResolvedValue({ projects: [reviewStale] })
    vi.mocked(api.projectBundleReviewPreview).mockResolvedValue({
      digest: 'sha256:binary',
      files: [
        reviewPreview.files[0],
        { path: '.kiro/settings/mcp.json', status: 'unreadable' as const, reason: 'binary' },
      ],
    })
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })

    const dialog = await openReviewDialog()
    const rows = await dialog.findAllByTestId('project-review-file')

    expect(within(rows[1]).getByText('Unreadable')).toBeInTheDocument()
    expect(within(rows[1]).getByText(/This file is not text, so its content cannot be shown or accepted/)).toBeInTheDocument()
    expect(within(rows[1]).getByText(/Replace it with a text file inside the Project, sync, and review again/)).toBeInTheDocument()
    expect(within(rows[1]).queryByLabelText(/Content of/)).not.toBeInTheDocument()
    // The path to fix stands on its own line with a copy affordance, so the
    // remedy is actionable without retyping.
    const fixPath = within(rows[1]).getByTestId('project-review-fix-path')
    expect(within(fixPath).getByText('.kiro/settings/mcp.json')).toBeInTheDocument()
    const copy = within(fixPath).getByRole('button', { name: 'Copy path' })
    fireEvent.click(copy)
    await waitFor(() => expect(writeText).toHaveBeenCalledWith('.kiro/settings/mcp.json'))
    expect(await within(fixPath).findByRole('button', { name: 'Path copied' })).toBeInTheDocument()

    expect(dialog.getByTestId('project-review-blocked')).toHaveTextContent('1 entry cannot be accepted until it is fixed')
    expect(dialog.getByRole('button', { name: 'Accept these changes' })).toBeDisabled()
  })

  it('shows each file with its status and content in the review dialog, and accepts the previewed digest', async () => {
    vi.mocked(api.projectBundles)
      .mockResolvedValueOnce({ projects: [reviewStale] })
      .mockResolvedValue({ projects: [managedProject] })
    vi.mocked(api.reviewProjectBundle).mockResolvedValue(managedProject)

    const dialog = await openReviewDialog()

    // Clicking "Review files" fetched the preview and posted nothing.
    await waitFor(() => expect(api.projectBundleReviewPreview).toHaveBeenCalledWith(reviewStale.id))
    expect(api.reviewProjectBundle).not.toHaveBeenCalled()
    expect(dialog.getByText('Review changed files in Shared Payments')).toBeInTheDocument()

    // Path, status badge, and the bytes being accepted, per file.
    const rows = await dialog.findAllByTestId('project-review-file')
    expect(rows).toHaveLength(3)
    expect(within(rows[0]).getByText('.kiro/settings/mcp.json')).toBeInTheDocument()
    expect(within(rows[0]).getByText('Changed')).toBeInTheDocument()
    expect(within(rows[0]).getByLabelText('Content of .kiro/settings/mcp.json')).toHaveTextContent('"command": "npx"')
    expect(within(rows[1]).getByText('Added')).toBeInTheDocument()
    expect(within(rows[1]).getByLabelText('Content of .kiro/agents/payments.md')).toHaveTextContent('Runs the refund reconciliation.')
    // Whole content, every time: no entry says a part of the file is missing.
    expect(dialog.queryByText(/Only the first part of this file/)).not.toBeInTheDocument()
    // A removed file has no content; the row says what accepting records.
    expect(within(rows[2]).getByText('Removed')).toBeInTheDocument()
    expect(within(rows[2]).queryByLabelText(/Content of/)).not.toBeInTheDocument()
    expect(within(rows[2]).getByText('This file was removed. Accepting records that it is gone.')).toBeInTheDocument()

    fireEvent.click(dialog.getByRole('button', { name: 'Accept these changes' }))

    // The accept carries the digest the owner was shown, nothing else.
    await waitFor(() => expect(api.reviewProjectBundle).toHaveBeenCalledWith(reviewStale.id, reviewPreview.digest))
    // The refetch returns a healthy Project: badge flips, notice clears, dialog closes.
    expect(await screen.findByText('Healthy')).toBeInTheDocument()
    expect(screen.queryByText('Review needed')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'New session' })).not.toBeDisabled()
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })

  it('reads as a first review when every file arrived with the Project', async () => {
    vi.mocked(api.projectBundles).mockResolvedValue({ projects: [reviewStale] })
    vi.mocked(api.projectBundleReviewPreview).mockResolvedValue({
      digest: 'sha256:first',
      files: reviewPreview.files.slice(0, 2).map(file => ({ ...file, status: 'added' as const })),
    })

    const dialog = await openReviewDialog()

    expect(await dialog.findByText('Review the files that can run code in Shared Payments')).toBeInTheDocument()
    expect(dialog.getByText(/These files arrived with the Project/)).toBeInTheDocument()
    expect(dialog.queryByText(/changed since you last accepted/)).not.toBeInTheDocument()
    expect(dialog.getAllByText('Added')).toHaveLength(2)
  })

  it('re-fetches the preview and says so when the files moved under the review', async () => {
    vi.mocked(api.projectBundles).mockResolvedValue({ projects: [reviewStale] })
    const movedPreview = {
      digest: 'sha256:moved',
      files: [{ ...reviewPreview.files[0], content: '{ "mcpServers": { "evil": { "command": "curl" } } }' }],
    }
    vi.mocked(api.projectBundleReviewPreview)
      .mockResolvedValueOnce(reviewPreview)
      .mockResolvedValue(movedPreview)
    vi.mocked(api.reviewProjectBundle).mockRejectedValueOnce(Object.assign(new Error('The files changed since this preview.'), {
      status: 409,
      body: JSON.stringify({ error: 'The files changed since this preview.', code: 'project_review_moved' }),
    }))

    const dialog = await openReviewDialog()
    await dialog.findAllByTestId('project-review-file')
    fireEvent.click(dialog.getByRole('button', { name: 'Accept these changes' }))

    await waitFor(() => expect(api.reviewProjectBundle).toHaveBeenCalledWith(reviewStale.id, reviewPreview.digest))
    // The rejection is explained in-dialog, the preview is re-read, and the
    // NEW bytes are what is now on screen.
    expect(await dialog.findByText(/These files changed again while you were reading/)).toBeInTheDocument()
    await waitFor(() => expect(api.projectBundleReviewPreview).toHaveBeenCalledTimes(2))
    expect(await dialog.findByText(/"evil"/)).toBeInTheDocument()
    expect(dialog.queryByText(/"atlassian"/)).not.toBeInTheDocument()
    // Nothing was recorded: the Project is still review-stale.
    expect(screen.getByRole('button', { name: 'New session' })).toBeDisabled()

    // A second accept carries the re-read digest.
    vi.mocked(api.reviewProjectBundle).mockResolvedValue(managedProject)
    fireEvent.click(dialog.getByRole('button', { name: 'Accept these changes' }))
    await waitFor(() => expect(api.reviewProjectBundle).toHaveBeenLastCalledWith(reviewStale.id, 'sha256:moved'))
  })

  it('withholds accept while an entry can never be accepted, and says why', async () => {
    vi.mocked(api.projectBundles).mockResolvedValue({ projects: [reviewStale] })
    vi.mocked(api.projectBundleReviewPreview).mockResolvedValue({
      digest: 'sha256:blocked',
      files: [
        reviewPreview.files[0],
        { path: '.kiro/skills/deploy/run.sh', status: 'unreadable' as const, reason: 'link-outside-root' },
      ],
    })

    const dialog = await openReviewDialog()
    const rows = await dialog.findAllByTestId('project-review-file')

    expect(within(rows[1]).getByText('Unreadable')).toBeInTheDocument()
    expect(within(rows[1]).getByText(/link that points outside the Project/)).toBeInTheDocument()
    // Every unreadable entry carries its path with a copy button.
    const fixPath = within(rows[1]).getByTestId('project-review-fix-path')
    expect(within(fixPath).getByText('.kiro/skills/deploy/run.sh')).toBeInTheDocument()
    expect(within(fixPath).getByRole('button', { name: 'Copy path' })).toBeInTheDocument()
    expect(dialog.getByTestId('project-review-blocked')).toHaveTextContent('1 entry cannot be accepted until it is fixed')
    const accept = dialog.getByRole('button', { name: 'Accept these changes' })
    expect(accept).toBeDisabled()
    fireEvent.click(accept)
    expect(api.reviewProjectBundle).not.toHaveBeenCalled()
  })

  it('renders a preview that could not be loaded as a failure with the hand-off', async () => {
    vi.mocked(api.projectBundles).mockResolvedValue({ projects: [reviewStale] })
    vi.mocked(api.projectBundleReviewPreview).mockRejectedValue(new Error('HTTP 500'))

    const dialog = await openReviewDialog()

    expect(await dialog.findByRole('alert')).toHaveTextContent('HTTP 500')
    expect(dialog.getByRole('button', { name: 'Accept these changes' })).toBeDisabled()
  })

  it('flags a sources-unavailable Project, lists the missing source ids in monospace, and blocks new sessions', async () => {
    const sourcesUnavailable = {
      ...managedProject,
      sources: [
        ...managedProject.sources,
        { id: 'payments-infra-1a2b3c4d', type: 'repo', url: 'https://github.com/acme/payments-infra', default_branch: 'main' },
      ],
      health: {
        status: 'sources_unavailable' as const,
        code: 'project_sources_unavailable',
        unavailable_sources: ['payments-infra-1a2b3c4d'],
      },
    }
    vi.mocked(api.projectBundles).mockResolvedValue({ projects: [sourcesUnavailable] })

    renderWithProviders(<ProjectBundlesPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Open project Shared Payments/ }))

    // Error badge with its own label, distinct from the review-stale copy.
    expect(screen.getByText('Source unavailable')).toBeInTheDocument()
    const alert = screen.getByRole('alert')
    // The action leads, then the manifest's path stands on its own line — the
    // local copy path the page shows, not a file the UI never surfaces.
    expect(alert).toHaveTextContent('Fix the source in the manifest, then sync.')
    expect(alert.textContent).toMatch(/\nManifest: \/data\/projects\/shared-payments\/project\.yaml\n/)
    expect(alert).toHaveTextContent('could not be cloned')
    // The failing source's synthesized id, rendered in monospace in the notice…
    const ids = screen.getAllByText('payments-infra-1a2b3c4d')
    expect(ids.some(el => el.className.includes('font-mono'))).toBe(true)
    // …and marked Unavailable INSIDE the Repositories card, so the row does not
    // read as healthy beside the banner that names it.
    const infraRow = ids.find(el => !el.className.includes('font-mono'))!.closest('div.rounded-md')!
    expect(within(infraRow).getByText('Unavailable')).toBeInTheDocument()
    expect(within(infraRow).getByText('https://github.com/acme/payments-infra')).toBeInTheDocument()
    const apiRow = screen.getByText('https://github.com/acme/payments-api').closest('div.rounded-md')!
    expect(within(apiRow).queryByText('Unavailable')).not.toBeInTheDocument()
    // Session start is blocked by the existing status !== 'healthy' guard.
    expect(screen.getByRole('button', { name: 'New session' })).toBeDisabled()
  })

  it('shows both the review-stale notice and the unavailable-source list when they co-occur', async () => {
    const combined = {
      ...managedProject,
      health: {
        status: 'review_stale' as const,
        code: 'project_review_stale',
        stale_files: ['.kiro/settings/mcp.json'],
        unavailable_sources: ['payments-infra-1a2b3c4d'],
      },
    }
    vi.mocked(api.projectBundles).mockResolvedValue({ projects: [combined] })

    renderWithProviders(<ProjectBundlesPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Open project Shared Payments/ }))

    // A stale digest outranks a missing secondary source: the badge is the
    // review-stale warning, not the error badge.
    expect(screen.getByText('Review needed')).toBeInTheDocument()
    expect(screen.queryByText('Source unavailable')).not.toBeInTheDocument()
    // Both notices render side by side.
    const alerts = screen.getAllByRole('alert')
    expect(alerts.length).toBeGreaterThanOrEqual(2)
    expect(screen.getByText('.kiro/settings/mcp.json')).toBeInTheDocument()
    expect(screen.getByText(/could not be cloned/)).toBeInTheDocument()
    expect(screen.getByText('payments-infra-1a2b3c4d')).toBeInTheDocument()
    // Sessions stay blocked while the digest is unreviewed.
    expect(screen.getByRole('button', { name: 'New session' })).toBeDisabled()
  })

  it('explains which files removal preserves', async () => {
    vi.mocked(api.projectBundles)
      .mockResolvedValueOnce({ projects: [localProject] })
      .mockResolvedValue({ projects: [] })
    renderWithProviders(<ProjectBundlesPage />)
    fireEvent.click(await screen.findByRole('button', { name: /Open project Payments Platform/ }))
    fireEvent.click(screen.getByRole('button', { name: 'Remove from Kiro Crew' }))

    expect(await screen.findByText(
      'Folders you added stay on disk. Kiro Crew removes only storage it created for this project.',
    )).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Remove project' }))

    await waitFor(() => expect(api.removeProjectBundle).toHaveBeenCalledWith(localProject.id))
    expect(await screen.findByText('No projects yet')).toBeInTheDocument()
  })
})
