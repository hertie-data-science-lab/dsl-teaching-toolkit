// The central toolkit repo: where the course set-up workflow runs (wizards/central) and where
// the catalogue's `orgs.yml` is read (model/catalogue).

export const CENTRAL = { owner: 'hertie-data-science-lab', repo: 'dsl-teaching-toolkit', workflow: 'bootstrap-org.yml', ref: 'main' } as const;
export const CENTRAL_ACTIONS = `https://github.com/${CENTRAL.owner}/${CENTRAL.repo}/actions/workflows/${CENTRAL.workflow}`;
