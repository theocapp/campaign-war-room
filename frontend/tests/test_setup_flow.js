/**
 * Integration tests for the Campaign Setup initialization flow.
 *
 * These are scenario-level tests covering the full user workflow across the
 * CampaignSetup page, complementing the unit-level tests in
 * src/pages/__tests__/CampaignSetup.test.tsx which cover individual behaviours.
 *
 * Each test exercises a complete user journey: render → interact → verify final state.
 * Written without JSX to work as a plain .js file.
 */

import React from 'react'
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { vi, describe, it, expect, beforeEach } from 'vitest'
import CampaignSetup from '../src/pages/CampaignSetup'

// ── Mock API ──────────────────────────────────────────────────────────────────

vi.mock('../src/api/client', () => ({
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

import { api } from '../src/api/client'

const mockApi = /** @type {any} */ (api)

// ── Fixtures ──────────────────────────────────────────────────────────────────

const emptyProfile = {
  id: 1, candidate_name: '', party: null, race: null, district: null,
  office: null, location: null, race_level: null, election_type: null,
  district_number: null, neighborhood_keywords: null, sparse_race_mode: false,
  election_date: null, campaign_message: null, key_priorities: null,
  relevance_keywords: null, excluded_keywords: null, geography_keywords: null,
  created_at: null, updated_at: null,
}

const filledProfile = {
  ...emptyProfile,
  candidate_name: 'Maria Alvarez',
  party: 'Democrat',
  office: 'City Council',
  district: 'District 7',
  location: 'Riverton, CA',
  election_date: '2026-11-03T00:00:00',
}

const sampleRace = {
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

const initSuccess = {
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

const initWithSkips = {
  ...initSuccess,
  steps: [
    { step: 1, label: 'Validate campaign', status: 'ok', detail: 'Profile ready for Maria Alvarez.' },
    { step: 2, label: 'Monitors created', status: 'skipped', detail: '0 monitors created, 5 already existed.' },
    { step: 3, label: 'Ingest coverage', status: 'ok', detail: '0 sources ingested from search monitors.' },
    { step: 4, label: 'Narrative refresh', status: 'ok', detail: '3 narrative(s) tracked.' },
  ],
  monitors_created: 0,
  monitors_skipped: 5,
  message: 'Campaign initialized for Maria Alvarez. 0 monitors, 0 sources, 3 narratives.',
}

function setup() {
  return { user: userEvent.setup() }
}

function renderSetup() {
  return render(React.createElement(CampaignSetup, null))
}

// ── Scenario 1: Full happy path from scratch ──────────────────────────────────

describe('Setup flow — fresh campaign initialization', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.getCampaign.mockResolvedValue(emptyProfile)
    mockApi.getRaces.mockResolvedValue([sampleRace])
    mockApi.getRace.mockResolvedValue(sampleRace)
    mockApi.updateCampaign.mockResolvedValue(filledProfile)
    mockApi.initializeCampaign.mockResolvedValue(initSuccess)
  })

  it('user fills name and party, clicks Initialize, sees all four steps', async () => {
    const { user } = setup()
    renderSetup()

    await user.type(await screen.findByTestId('candidate-name'), 'Maria Alvarez')
    await user.type(screen.getByTestId('party'), 'Democrat')
    await user.click(screen.getByTestId('initialize-btn'))

    const result = await screen.findByTestId('init-result')
    expect(within(result).getByText('Validate campaign')).toBeInTheDocument()
    expect(within(result).getByText('Monitors created')).toBeInTheDocument()
    expect(within(result).getByText('Ingest coverage')).toBeInTheDocument()
    expect(within(result).getByText('Narrative refresh')).toBeInTheDocument()
  })

  it('profile is saved before initialize is called (auto-save order)', async () => {
    const { user } = setup()
    const callOrder = []
    mockApi.updateCampaign.mockImplementation(() => {
      callOrder.push('save')
      return Promise.resolve(filledProfile)
    })
    mockApi.initializeCampaign.mockImplementation(() => {
      callOrder.push('init')
      return Promise.resolve(initSuccess)
    })

    renderSetup()
    await user.type(await screen.findByTestId('candidate-name'), 'Maria Alvarez')
    await user.click(screen.getByTestId('initialize-btn'))

    await waitFor(() => {
      expect(callOrder).toEqual(['save', 'init'])
    })
  })

  it('shows the success message from the API in the UI', async () => {
    const { user } = setup()
    renderSetup()

    await user.type(await screen.findByTestId('candidate-name'), 'Maria Alvarez')
    await user.click(screen.getByTestId('initialize-btn'))

    expect(await screen.findByText('Campaign initialized successfully.')).toBeInTheDocument()
  })

  it('displays tick icon for each successful step', async () => {
    const { user } = setup()
    renderSetup()

    await user.type(await screen.findByTestId('candidate-name'), 'Maria Alvarez')
    await user.click(screen.getByTestId('initialize-btn'))

    const result = await screen.findByTestId('init-result')
    const ticks = within(result).getAllByText('✓')
    expect(ticks.length).toBe(4)
  })
})

// ── Scenario 2: Race directory → auto-populate → initialize ──────────────────

describe('Setup flow — race selection before initialization', () => {
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
    mockApi.updateCampaign.mockResolvedValue(filledProfile)
    mockApi.initializeCampaign.mockResolvedValue(initSuccess)
  })

  it('selecting a race pre-fills candidate name, then Initialize works', async () => {
    const { user } = setup()
    renderSetup()

    const selectBtn = await screen.findByText('Select Race for Workspace')
    await user.click(selectBtn)

    await waitFor(() => {
      const nameInput = /** @type {HTMLInputElement} */ (screen.getByTestId('candidate-name'))
      expect(nameInput.value).toBe('Maria Alvarez')
    })

    await user.click(screen.getByTestId('initialize-btn'))

    await screen.findByTestId('init-result')
    expect(mockApi.initializeCampaign).toHaveBeenCalledOnce()
  })

  it('race selection confirmation message is visible before initialization', async () => {
    const { user } = setup()
    renderSetup()

    await user.click(await screen.findByText('Select Race for Workspace'))
    expect(await screen.findByText(/Race selected\./)).toBeInTheDocument()

    await user.click(screen.getByTestId('initialize-btn'))
    await screen.findByTestId('init-result')
  })
})

// ── Scenario 3: Idempotent re-run ─────────────────────────────────────────────

describe('Setup flow — idempotent re-initialization', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.getCampaign.mockResolvedValue(filledProfile)
    mockApi.getRaces.mockResolvedValue([sampleRace])
    mockApi.updateCampaign.mockResolvedValue(filledProfile)
  })

  it('clicking Initialize twice shows skipped monitors on second run', async () => {
    const { user } = setup()
    mockApi.initializeCampaign
      .mockResolvedValueOnce(initSuccess)
      .mockResolvedValueOnce(initWithSkips)

    renderSetup()
    const btn = await screen.findByTestId('initialize-btn')

    await user.click(btn)
    await screen.findByText('Campaign initialized successfully.')

    await user.click(btn)
    await waitFor(() => {
      expect(mockApi.initializeCampaign).toHaveBeenCalledTimes(2)
    })

    const result = screen.getByTestId('init-result')
    expect(within(result).getByText('Monitors created')).toBeInTheDocument()
    expect(within(result).getByText('–')).toBeInTheDocument()
  })

  it('Initialize button is re-enabled between runs', async () => {
    const { user } = setup()
    mockApi.initializeCampaign.mockResolvedValue(initSuccess)

    renderSetup()
    const btn = await screen.findByTestId('initialize-btn')

    await user.click(btn)
    await screen.findByTestId('init-result')

    expect(btn).not.toBeDisabled()
  })
})

// ── Scenario 4: Error recovery ────────────────────────────────────────────────

describe('Setup flow — error recovery', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.getCampaign.mockResolvedValue(filledProfile)
    mockApi.getRaces.mockResolvedValue([])
    mockApi.updateCampaign.mockResolvedValue(filledProfile)
  })

  it('after API failure, user can retry and succeed', async () => {
    const { user } = setup()
    mockApi.initializeCampaign
      .mockRejectedValueOnce(new Error('LLM unavailable'))
      .mockResolvedValueOnce(initSuccess)

    renderSetup()
    const btn = await screen.findByTestId('initialize-btn')

    await user.click(btn)
    expect(await screen.findByTestId('init-error')).toHaveTextContent('LLM unavailable')

    await user.click(btn)
    await screen.findByTestId('init-result')
    expect(screen.queryByTestId('init-error')).not.toBeInTheDocument()
  })

  it('save failure blocks initialize and shows message', async () => {
    const { user } = setup()
    mockApi.updateCampaign.mockRejectedValue(new Error('Network error'))

    renderSetup()
    await user.click(await screen.findByTestId('initialize-btn'))

    expect(await screen.findByTestId('init-error')).toHaveTextContent('Network error')
    expect(mockApi.initializeCampaign).not.toHaveBeenCalled()
  })
})

// ── Scenario 5: Advanced settings persist through toggle ─────────────────────

describe('Setup flow — advanced settings round-trip', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockApi.getCampaign.mockResolvedValue(emptyProfile)
    mockApi.getRaces.mockResolvedValue([])
    mockApi.updateCampaign.mockResolvedValue(filledProfile)
    mockApi.initializeCampaign.mockResolvedValue(initSuccess)
  })

  it('values typed in advanced fields persist after closing and reopening', async () => {
    const { user } = setup()
    renderSetup()

    await user.type(await screen.findByTestId('candidate-name'), 'Maria Alvarez')

    const toggle = screen.getByTestId('advanced-toggle')
    await user.click(toggle)

    const officeInput = screen.getByPlaceholderText('e.g. City Council Member')
    await user.type(officeInput, 'City Council')

    await user.click(toggle)
    expect(screen.queryByPlaceholderText('e.g. City Council Member')).not.toBeInTheDocument()

    await user.click(toggle)
    const officeInputAgain = /** @type {HTMLInputElement} */ (
      screen.getByPlaceholderText('e.g. City Council Member')
    )
    expect(officeInputAgain.value).toBe('City Council')
  })

  it('Initialize payload includes values from advanced fields', async () => {
    const { user } = setup()
    renderSetup()

    await user.type(await screen.findByTestId('candidate-name'), 'Maria Alvarez')

    const toggle = screen.getByTestId('advanced-toggle')
    await user.click(toggle)
    await user.type(screen.getByPlaceholderText('e.g. City Council Member'), 'City Council')
    await user.click(toggle)

    await user.click(screen.getByTestId('initialize-btn'))

    await screen.findByTestId('init-result')
    expect(mockApi.updateCampaign).toHaveBeenCalledWith(
      expect.objectContaining({ candidate_name: 'Maria Alvarez', office: 'City Council' })
    )
  })
})
