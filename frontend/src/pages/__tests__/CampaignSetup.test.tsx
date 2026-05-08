import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, describe, it, expect, beforeEach } from 'vitest'
import CampaignSetup from '../CampaignSetup'
import type { CampaignProfile, RaceDirectory, CampaignInitializeResult } from '../../api/types'

// ── Mock API client ──────────────────────────────────────────────────────────

vi.mock('../../api/client', () => ({
  api: {
    getCampaign: vi.fn(),
    updateCampaign: vi.fn(),
    initializeCampaign: vi.fn(),
    getRaces: vi.fn(),
    getRace: vi.fn(),
    selectRace: vi.fn(),
    resetWorkspace: vi.fn(),
    importRaceCSV: vi.fn(),
  },
}))

import { api } from '../../api/client'
const mockApi = api as unknown as {
  getCampaign: ReturnType<typeof vi.fn>
  updateCampaign: ReturnType<typeof vi.fn>
  initializeCampaign: ReturnType<typeof vi.fn>
  getRaces: ReturnType<typeof vi.fn>
  getRace: ReturnType<typeof vi.fn>
  selectRace: ReturnType<typeof vi.fn>
  resetWorkspace: ReturnType<typeof vi.fn>
  importRaceCSV: ReturnType<typeof vi.fn>
}

// ── Fixtures ─────────────────────────────────────────────────────────────────

const emptyProfile: CampaignProfile = {
  id: 1, candidate_name: '', party: null, race: null, district: null,
  office: null, location: null, race_level: null, election_type: null,
  district_number: null, neighborhood_keywords: null, sparse_race_mode: false,
  election_date: null, campaign_message: null, key_priorities: null,
  relevance_keywords: null, excluded_keywords: null, geography_keywords: null,
  created_at: null, updated_at: null,
}

const filledProfile: CampaignProfile = {
  ...emptyProfile,
  candidate_name: 'Maria Alvarez',
  party: 'Democrat',
  office: 'City Council',
  district: 'District 7',
  location: 'Riverton, CA',
  election_date: '2026-11-03T00:00:00',
  election_date_inferred: true,
}

const sampleRace: RaceDirectory = {
  id: 42,
  race_key: 'ca-12-house-2026',
  race_name: 'CA-12 U.S. House 2026',
  race_level: 'federal',
  office_name: 'U.S. Representative',
  state: 'CA',
  district_label: 'CA-12',
  district_number: '12',
  election_type: 'general',
  election_date: '2026-11-03T00:00:00',
  geography_summary: 'San Francisco Bay Area',
  data_source: 'fec',
  is_active: true,
  created_at: null,
  updated_at: null,
  candidates: [
    { id: 101, race_id: 42, candidate_name: 'Maria Alvarez', party: 'Democrat', is_incumbent: false, role: 'candidate', campaign_url: null, notes: null, created_at: null, updated_at: null },
    { id: 102, race_id: 42, candidate_name: 'Roy Harmon', party: 'Republican', is_incumbent: true, role: 'opponent', campaign_url: null, notes: null, created_at: null, updated_at: null },
  ],
}

const initSuccess: CampaignInitializeResult = {
  steps: [
    { step: 1, label: 'Validate campaign', status: 'ok', detail: 'Profile ready for Maria Alvarez.' },
    { step: 2, label: 'Monitors created', status: 'ok', detail: '5 monitors created, 0 already existed.' },
    { step: 3, label: 'Ingest coverage', status: 'ok', detail: '12 sources ingested from search monitors.' },
    { step: 4, label: 'Narrative refresh', status: 'ok', detail: '3 narrative(s) tracked.' },
  ],
  monitors_created: 5,
  monitors_skipped: 0,
  sources_ingested: 12,
  narratives_refreshed: 3,
  message: 'Campaign initialized successfully.',
  initialized_at: '2026-05-08T12:00:00',
}

// ── Tests ─────────────────────────────────────────────────────────────────────

describe('CampaignSetup — default view', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.getCampaign.mockResolvedValue(emptyProfile)
    mockApi.getRaces.mockResolvedValue([sampleRace])
    mockApi.getRace.mockResolvedValue(sampleRace)
  })

  it('renders candidate name and party fields by default', async () => {
    render(<CampaignSetup />)
    expect(await screen.findByTestId('candidate-name')).toBeInTheDocument()
    expect(screen.getByTestId('party')).toBeInTheDocument()
  })

  it('renders the Initialize Campaign button by default', async () => {
    render(<CampaignSetup />)
    expect(await screen.findByTestId('initialize-btn')).toBeInTheDocument()
  })

  it('does NOT show advanced sections by default', async () => {
    render(<CampaignSetup />)
    await screen.findByTestId('candidate-name')
    expect(screen.queryByText('Core Campaign Message')).not.toBeInTheDocument()
    expect(screen.queryByText('Campaign Filters')).not.toBeInTheDocument()
    expect(screen.queryByText('Key Priorities')).not.toBeInTheDocument()
    expect(screen.queryByText('Race Type')).not.toBeInTheDocument()
  })

  it('shows race setup section with FEC directory by default', async () => {
    render(<CampaignSetup />)
    expect(await screen.findByText('1. Pick Your Race')).toBeInTheDocument()
    expect(screen.getByText('FEC Directory')).toBeInTheDocument()
  })

  it('loads and displays races on mount', async () => {
    render(<CampaignSetup />)
    // race name appears in both the list button and the auto-selected detail panel
    const matches = await screen.findAllByText('CA-12 U.S. House 2026')
    expect(matches.length).toBeGreaterThanOrEqual(1)
  })
})

describe('CampaignSetup — advanced toggle', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.getCampaign.mockResolvedValue(filledProfile)
    mockApi.getRaces.mockResolvedValue([sampleRace])
    mockApi.getRace.mockResolvedValue(sampleRace)
  })

  it('toggles advanced sections when clicking the Advanced button', async () => {
    const user = userEvent.setup()
    render(<CampaignSetup />)

    const toggle = await screen.findByTestId('advanced-toggle')
    expect(screen.queryByText('Core Campaign Message')).not.toBeInTheDocument()

    await user.click(toggle)

    expect(screen.getByText('Core Campaign Message')).toBeInTheDocument()
    expect(screen.getByText('Campaign Filters')).toBeInTheDocument()
    expect(screen.getByText('Key Priorities')).toBeInTheDocument()
    expect(screen.getByText('Race Type')).toBeInTheDocument()
  })

  it('hides advanced sections again when clicking Advanced a second time', async () => {
    const user = userEvent.setup()
    render(<CampaignSetup />)

    const toggle = await screen.findByTestId('advanced-toggle')
    await user.click(toggle)
    expect(screen.getByText('Core Campaign Message')).toBeInTheDocument()

    await user.click(toggle)
    expect(screen.queryByText('Core Campaign Message')).not.toBeInTheDocument()
  })

  it('shows Save Profile button only in advanced section', async () => {
    const user = userEvent.setup()
    render(<CampaignSetup />)

    expect(screen.queryByText('Save Profile')).not.toBeInTheDocument()

    const toggle = await screen.findByTestId('advanced-toggle')
    await user.click(toggle)

    expect(screen.getByText('Save Profile')).toBeInTheDocument()
  })

  it('shows Workspace Reset only in advanced section', async () => {
    const user = userEvent.setup()
    render(<CampaignSetup />)

    expect(screen.queryByText(/Workspace Reset/)).not.toBeInTheDocument()

    const toggle = await screen.findByTestId('advanced-toggle')
    await user.click(toggle)

    expect(screen.getByText(/Workspace Reset/)).toBeInTheDocument()
  })

  it('shows CSV import only in advanced section', async () => {
    const user = userEvent.setup()
    render(<CampaignSetup />)

    expect(screen.queryByText('Import Race Setup from CSV')).not.toBeInTheDocument()

    const toggle = await screen.findByTestId('advanced-toggle')
    await user.click(toggle)

    expect(screen.getByText('Import Race Setup from CSV')).toBeInTheDocument()
  })

  it('shows inferred badge only when election_date_inferred is true', async () => {
    const user = userEvent.setup()
    mockApi.getCampaign.mockResolvedValueOnce({
      ...filledProfile,
      election_date: '2026-11-03T00:00:00',
      election_date_inferred: false,
    })
    render(<CampaignSetup />)

    const toggle = await screen.findByTestId('advanced-toggle')
    await user.click(toggle)

    expect(screen.queryByText('· inferred')).not.toBeInTheDocument()
  })
})

describe('CampaignSetup — initialization happy path', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.getCampaign.mockResolvedValue(filledProfile)
    mockApi.getRaces.mockResolvedValue([sampleRace])
    mockApi.updateCampaign.mockResolvedValue(filledProfile)
    mockApi.initializeCampaign.mockResolvedValue(initSuccess)
  })

  it('auto-saves profile then calls initializeCampaign', async () => {
    const user = userEvent.setup()
    render(<CampaignSetup />)

    const btn = await screen.findByTestId('initialize-btn')
    await user.click(btn)

    await waitFor(() => {
      expect(mockApi.updateCampaign).toHaveBeenCalledOnce()
      expect(mockApi.initializeCampaign).toHaveBeenCalledOnce()
    })
  })

  it('displays step results after initialization completes', async () => {
    const user = userEvent.setup()
    render(<CampaignSetup />)

    const btn = await screen.findByTestId('initialize-btn')
    await user.click(btn)

    const result = await screen.findByTestId('init-result')
    expect(within(result).getByText('Validate campaign')).toBeInTheDocument()
    expect(within(result).getByText('Monitors created')).toBeInTheDocument()
    expect(within(result).getByText('Ingest coverage')).toBeInTheDocument()
    expect(within(result).getByText('Narrative refresh')).toBeInTheDocument()
  })

  it('displays the success message after initialization', async () => {
    const user = userEvent.setup()
    render(<CampaignSetup />)

    await user.click(await screen.findByTestId('initialize-btn'))

    expect(await screen.findByText('Campaign initialized successfully.')).toBeInTheDocument()
  })

  it('clears initialization ticker on unmount', async () => {
    const user = userEvent.setup()
    let resolveInit!: (value: CampaignInitializeResult) => void
    const initPromise = new Promise<CampaignInitializeResult>((resolve) => {
      resolveInit = resolve
    })
    mockApi.initializeCampaign.mockReturnValueOnce(initPromise)
    const clearIntervalSpy = vi.spyOn(globalThis, 'clearInterval')

    const { unmount } = render(<CampaignSetup />)
    await user.click(await screen.findByTestId('initialize-btn'))

    unmount()
    resolveInit(initSuccess)

    await waitFor(() => expect(clearIntervalSpy).toHaveBeenCalled())
    clearIntervalSpy.mockRestore()
  })
})

describe('CampaignSetup — validation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.getCampaign.mockResolvedValue(emptyProfile)
    mockApi.getRaces.mockResolvedValue([])
    mockApi.updateCampaign.mockResolvedValue(emptyProfile)
    mockApi.initializeCampaign.mockResolvedValue(initSuccess)
  })

  it('blocks initialization and shows error when candidate name is empty', async () => {
    const user = userEvent.setup()
    render(<CampaignSetup />)

    const btn = await screen.findByTestId('initialize-btn')
    await user.click(btn)

    expect(await screen.findByTestId('init-error')).toHaveTextContent(
      'Candidate name is required before initializing.'
    )
    expect(mockApi.updateCampaign).not.toHaveBeenCalled()
    expect(mockApi.initializeCampaign).not.toHaveBeenCalled()
  })

  it('allows initialization after candidate name is entered', async () => {
    const user = userEvent.setup()
    render(<CampaignSetup />)

    const nameInput = await screen.findByTestId('candidate-name')
    await user.type(nameInput, 'Jane Smith')

    await user.click(screen.getByTestId('initialize-btn'))

    await waitFor(() => expect(mockApi.initializeCampaign).toHaveBeenCalledOnce())
  })
})

describe('CampaignSetup — race selection', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.getCampaign.mockResolvedValue(emptyProfile)
    mockApi.getRaces.mockResolvedValue([sampleRace])
    mockApi.getRace.mockResolvedValue(sampleRace)
    mockApi.selectRace.mockResolvedValue({
      race: sampleRace,
      campaign: filledProfile,
      selected_candidate_name: 'Maria Alvarez',
      opponents_created: 1,
      opponents_updated: 0,
      message: 'Race selected.',
    })
  })

  it('clicking a race in the list invokes getRace for details', async () => {
    const user = userEvent.setup()
    render(<CampaignSetup />)

    // The race list renders a button; getAllByText since the name appears in list + auto-selected detail panel
    const raceButtons = await screen.findAllByText('CA-12 U.S. House 2026')
    await user.click(raceButtons[0])

    await waitFor(() => expect(mockApi.getRace).toHaveBeenCalledWith(42))
  })

  it('detail panel shows geography summary after race is selected', async () => {
    render(<CampaignSetup />)
    // auto-selected on mount via chooseRace(results[0])
    await waitFor(() => {
      expect(screen.getByText('San Francisco Bay Area')).toBeInTheDocument()
    })
  })

  it('selecting a race fills the candidate name field', async () => {
    const user = userEvent.setup()
    render(<CampaignSetup />)

    const selectBtn = await screen.findByText('Select Race for Workspace')
    await user.click(selectBtn)

    await waitFor(() => {
      const nameInput = screen.getByTestId('candidate-name') as HTMLInputElement
      expect(nameInput.value).toBe('Maria Alvarez')
    })
  })

  it('shows race select confirmation message after selection', async () => {
    const user = userEvent.setup()
    render(<CampaignSetup />)

    await user.click(await screen.findByText('Select Race for Workspace'))

    expect(await screen.findByText(/Race selected\./)).toBeInTheDocument()
  })
})

describe('CampaignSetup — initialization API error', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.getCampaign.mockResolvedValue(filledProfile)
    mockApi.getRaces.mockResolvedValue([])
    mockApi.updateCampaign.mockResolvedValue(filledProfile)
    mockApi.initializeCampaign.mockRejectedValue(new Error('LLM unavailable'))
  })

  it('shows the API error message when initialization fails', async () => {
    const user = userEvent.setup()
    render(<CampaignSetup />)

    await user.click(await screen.findByTestId('initialize-btn'))

    expect(await screen.findByTestId('init-error')).toHaveTextContent('LLM unavailable')
  })

  it('re-enables the Initialize button after failure', async () => {
    const user = userEvent.setup()
    render(<CampaignSetup />)

    await user.click(await screen.findByTestId('initialize-btn'))
    await screen.findByTestId('init-error')

    expect(screen.getByTestId('initialize-btn')).not.toBeDisabled()
  })
})
