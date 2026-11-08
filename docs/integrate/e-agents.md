# E. Agents over gold

An MCP server can expose a gold folder to AI agents as read-only tools: list the datasets, read one with its schema, and refuse what the caller's audience may not see. The [base page](README.md) defines the marks. meterline has no such server.

The table below lists its steps. Every step is the adopter's pattern or new code, and the table says which.

| Step | How | Maturity |
|---|---|---|
| Run gold | any of scenarios [A](a-one-machine.md) or [B](b-kubernetes.md); the server reads `published/<audience>` and never `builds/` | pattern |
| The server | a read-only MCP server that lists the datasets, reads one with the schema that `meterline-gold schemas` describes, and applies `servable()` to every request | new code |
| One audience per agent | start one server per audience, or map an agent's identity to an audience, so that an agent never holds a wider folder than its reader's | new code |
| Review what an agent may ask | decide which audiences an agent may be given; `central/` is for the central team only | pattern |

**Least mature step:** new code

The server would sit on top of A or B and be small, because gold already holds the data, the schemas and the access rules. Until it exists, an agent can read `published/<audience>` files directly, through the same `servable(path, audience)` check that [example 05](../../examples/05-dashboard/README.md) applies to a browser.
