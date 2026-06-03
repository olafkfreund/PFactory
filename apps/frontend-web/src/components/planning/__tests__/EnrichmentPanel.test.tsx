/**
 * @vitest-environment jsdom
 *
 * Tests for <EnrichmentPanel>:
 *  - renders infra findings, resource keys, and public-SG exposure rows
 *  - renders best-practice link (with href) and wiki-doc title
 *  - renders the muted empty-state when enrichment arrays are empty
 *  - does not crash on absent/partial enrichment fields
 */

import { describe, it, expect } from 'vitest';
import '@testing-library/jest-dom/vitest';
import { render, screen } from '@testing-library/react';

import { EnrichmentPanel } from '../EnrichmentPanel';
import type { PlanSession } from '../../../shared/types/plan';

// ── Fixtures ────────────────────────────────────────────────────────────────

function makeSession(overrides: Partial<PlanSession['plan']['enrichment']> = {}): PlanSession {
  return {
    session_id: 'sess-test',
    status: 'processed',
    plan: {
      plan_id: 'p1',
      title: 'Test Plan',
      description: 'A test plan',
      source_format: 'text',
      source_channel: null,
      criteria: [],
      target_kind: 'software',
      plan_type: 'feature',
      content_hash: 'abc',
      ingested_at: '2026-06-01T00:00:00Z',
      enrichment: {
        infra: [],
        knowledge: [],
        ...overrides,
      },
    },
    epic: null,
    artifacts: [],
    review: null,
    emit_result: null,
    created_at: '2026-06-01T00:00:00Z',
  };
}

const FULL_ENRICHMENT: PlanSession['plan']['enrichment'] = {
  infra: [
    {
      adapter: 'aws',
      target: 'us-east-1',
      available: true,
      workloads: [],
      resources: {
        ec2_instance_count: 12,
        eks_cluster_count: 2,
        security_group_count: 34,
        regions: 'us-east-1,eu-west-1',
      },
      policies: [
        {
          kind: 'security_group',
          group_id: 'sg-0abc123',
          group_name: 'wide-open-sg',
          public: true,
          open_cidrs: ['0.0.0.0/0'],
          open_ports: ['22', '3389'],
        },
      ],
      load: {},
      findings: [
        'Found 2 publicly accessible EKS clusters',
        '34 security groups, 1 with public inbound rules',
      ],
      error: null,
    },
  ],
  knowledge: [
    {
      connector: 'best-practices',
      kind: 'best-practice',
      title: 'AWS Security Hardening Guide',
      uri: 'https://example.com/aws-hardening',
      snippet: 'Always restrict security group ingress to known CIDR blocks.',
      score: 0.92,
      metadata: {},
    },
    {
      connector: 'git-markdown',
      kind: 'doc',
      title: 'Internal Wiki: EKS Setup',
      uri: 'https://internal.wiki/eks-setup',
      snippet: 'This page describes the EKS cluster provisioning process.',
      score: 0.75,
      metadata: {},
    },
  ],
};

// ── Tests ───────────────────────────────────────────────────────────────────

describe('<EnrichmentPanel> — full enrichment', () => {
  it('renders infra probe section with findings', () => {
    render(<EnrichmentPanel session={makeSession(FULL_ENRICHMENT)} />);

    expect(screen.getByTestId('enrichment-panel')).toBeInTheDocument();
    expect(screen.getByTestId('infra-card')).toBeInTheDocument();

    // Findings list
    const findingsList = screen.getByTestId('infra-findings');
    expect(findingsList).toHaveTextContent('Found 2 publicly accessible EKS clusters');
    expect(findingsList).toHaveTextContent('34 security groups, 1 with public inbound rules');
  });

  it('renders resource key/value grid', () => {
    render(<EnrichmentPanel session={makeSession(FULL_ENRICHMENT)} />);

    const resources = screen.getByTestId('infra-resources');
    expect(resources).toHaveTextContent('ec2_instance_count');
    expect(resources).toHaveTextContent('12');
    expect(resources).toHaveTextContent('eks_cluster_count');
    expect(resources).toHaveTextContent('2');
  });

  it('renders the public SG exposure row', () => {
    render(<EnrichmentPanel session={makeSession(FULL_ENRICHMENT)} />);

    const exposures = screen.getByTestId('infra-exposures');
    // The exposure row should show the group name and group id
    expect(exposures).toHaveTextContent('wide-open-sg');
    expect(exposures).toHaveTextContent('sg-0abc123');
    // Open CIDRs and ports
    expect(exposures).toHaveTextContent('0.0.0.0/0');
    expect(exposures).toHaveTextContent('22');
    expect(exposures).toHaveTextContent('3389');
  });

  it('renders the best-practice link with href', () => {
    render(<EnrichmentPanel session={makeSession(FULL_ENRICHMENT)} />);

    // Multiple knowledge-link elements may exist (best-practice + wiki doc both have URIs)
    const links = screen.getAllByTestId('knowledge-link');
    const bpLink = links.find((el) => el.textContent?.includes('AWS Security Hardening Guide'));
    expect(bpLink).toBeInTheDocument();
    expect(bpLink).toHaveAttribute('href', 'https://example.com/aws-hardening');
    expect(bpLink).toHaveAttribute('target', '_blank');
    expect(bpLink).toHaveAttribute('rel', 'noopener noreferrer');
  });

  it('renders the best-practice snippet', () => {
    render(<EnrichmentPanel session={makeSession(FULL_ENRICHMENT)} />);

    const snippets = screen.getAllByTestId('knowledge-snippet');
    const bpSnippet = snippets.find((el) =>
      el.textContent?.includes('Always restrict security group'),
    );
    expect(bpSnippet).toBeInTheDocument();
  });

  it('renders the wiki doc title in the Wiki & docs section', () => {
    render(<EnrichmentPanel session={makeSession(FULL_ENRICHMENT)} />);

    // Multiple knowledge-link elements may exist (best-practice + doc)
    const links = screen.getAllByTestId('knowledge-link');
    const wikiLink = links.find((el) => el.textContent?.includes('EKS Setup'));
    expect(wikiLink).toBeInTheDocument();
    expect(wikiLink).toHaveAttribute('href', 'https://internal.wiki/eks-setup');
  });

  it('shows adapter availability badge', () => {
    render(<EnrichmentPanel session={makeSession(FULL_ENRICHMENT)} />);
    expect(screen.getByTestId('infra-available')).toBeInTheDocument();
  });

  it('shows relevance score badges', () => {
    render(<EnrichmentPanel session={makeSession(FULL_ENRICHMENT)} />);
    const scores = screen.getAllByTestId('knowledge-score');
    expect(scores.length).toBeGreaterThanOrEqual(1);
    expect(scores[0]).toHaveTextContent('0.92');
  });
});

describe('<EnrichmentPanel> — empty enrichment', () => {
  it('renders the empty-state placeholder when both arrays are empty', () => {
    render(<EnrichmentPanel session={makeSession()} />);
    expect(screen.getByTestId('enrichment-empty')).toBeInTheDocument();
    expect(screen.queryByTestId('enrichment-panel')).not.toBeInTheDocument();
  });

  it('does not crash when enrichment is empty', () => {
    expect(() => render(<EnrichmentPanel session={makeSession()} />)).not.toThrow();
  });
});

describe('<EnrichmentPanel> — resilience', () => {
  it('renders without crashing when infra fields are missing/null', () => {
    const snap = {
      adapter: 'gcp',
      target: '',
      available: false,
      workloads: [],
      resources: {},
      policies: [],
      load: {},
      findings: [],
      error: 'adapter timed out',
    };
    expect(() =>
      render(<EnrichmentPanel session={makeSession({ infra: [snap], knowledge: [] })} />),
    ).not.toThrow();
    expect(screen.getByTestId('infra-error')).toHaveTextContent('adapter timed out');
    expect(screen.getByTestId('infra-unavailable')).toBeInTheDocument();
  });

  it('renders without crashing when knowledge fields are partially missing', () => {
    const ref_ = {
      connector: 'backstage',
      kind: 'catalog' as const,
      title: 'Service Catalog Entry',
      uri: '',
      snippet: '',
      score: 0.5,
      metadata: {},
    };
    expect(() =>
      render(<EnrichmentPanel session={makeSession({ infra: [], knowledge: [ref_] })} />),
    ).not.toThrow();
    // No URI → renders a plain <p> with data-testid="knowledge-title", not a link
    expect(screen.getByTestId('knowledge-title')).toHaveTextContent('Service Catalog Entry');
  });

  it('renders infra-only enrichment without crashing', () => {
    const infraOnly = { infra: FULL_ENRICHMENT.infra, knowledge: [] };
    expect(() =>
      render(<EnrichmentPanel session={makeSession(infraOnly)} />),
    ).not.toThrow();
    expect(screen.getByTestId('infra-card')).toBeInTheDocument();
  });

  it('renders knowledge-only enrichment without crashing', () => {
    const kOnly = { infra: [], knowledge: FULL_ENRICHMENT.knowledge };
    expect(() =>
      render(<EnrichmentPanel session={makeSession(kOnly)} />),
    ).not.toThrow();
    expect(screen.getAllByTestId('knowledge-card').length).toBeGreaterThanOrEqual(1);
  });
});
