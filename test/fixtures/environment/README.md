# Environment fixtures

`ext-environment-opensim.xml` is the body of a GET on the `ExtEnvironment`
capability, captured 2026-09-06 from the local OpenSim test region while the
agent was standing in it. Nothing is redacted: it is the grid's default day
cycle plus the region's own id.

Two things about capturing it, both of which cost a round:

- The capability answers **503** until the agent is actually in the region.
  Resolving the URL from the seed capability straight after login and fetching
  it works in the sense that both steps succeed, and the reply is a service
  error that looks like a broken URL rather than like being early.
- The same region also offers `EnvironmentSettings`, the legacy Windlight
  document, in a completely different shape. It is not what this parses.
