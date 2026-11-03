# Security policy

meterline reads usage data from provider APIs with credentials you supply (an Anthropic Admin key, an Anthropic Analytics key, a LiteLLM admin key, a GitHub token). `meterline-gold` also uses a hashing key that you make yourself, `METERLINE_GOLD_KEY`. A flaw that exposes one of those keys, or the usage data a run writes, is a security issue. The Anthropic Admin key is the strongest of them: it cannot be limited to reading, and it can also manage members, workspaces and API keys (the README of `meterline-anthropic-admin` says what to do about that).

## Reporting a vulnerability

Report it privately, using GitHub's private vulnerability reporting: open the repository's **Security** tab and choose **Report a vulnerability**. This creates a security advisory that only you and the maintainer can see.

**Do not open a public issue or pull request for a vulnerability.** Include the package and version, what you did, what happened, and what you expected. Use invented data only; never attach a real export, key or token. If you posted a real credential by mistake, revoke it at the provider first.

This is a one-person project: reports are handled on a best-effort basis, with no fixed response time.

## Supported versions

Only the latest released version of each package gets security fixes. Older versions are not patched; upgrade to the latest release.

| Package | Fixes go to |
|---|---|
| `meterline`, `meterline-anthropic`, `meterline-anthropic-admin`, `meterline-litellm`, `meterline-copilot`, `meterline-sim`, `meterline-gold`, `meterline-job` | the latest release |

## Scope

In scope: handling of keys and tokens (`ANTHROPIC_ADMIN_KEY`, `ANTHROPIC_ANALYTICS_KEY`, `LITELLM_API_KEY`, `COPILOT_TOKEN`, `COPILOT_BILLING_TOKEN` and the gold hashing key `METERLINE_GOLD_KEY`; they are read from the environment and must never reach a file, a log line or a manifest), the data written under a data directory, and the parsing of provider responses. Out of scope: weaknesses in the provider APIs themselves, and a data directory or gold folder that you share or make readable to others (meterline writes them readable by their owner only; the data directory holds email addresses as the provider returned them).

## The gold hashing key

`METERLINE_GOLD_KEY` is the key that turns every person and actor id into a pseudonym in the gold folder (HMAC-SHA256). Whoever holds it and a list of ids can recompute the pseudonyms, so keep it as closed as `delivery/`, the folder that holds names and emails.

- **How it is made.** meterline does not generate it. You invent one: random, at least 32 bytes (`python3 -c "import secrets; print(secrets.token_urlsafe(48))"` gives 64 characters). A shorter key is refused and the build exits with an error.
- **Where it lives.** In the environment of the process that runs `meterline-gold build`; from Python, `meterline_gold.build` takes it as an argument. meterline never writes it to a file, a log line or a manifest. Keep it apart from the outputs and out of git. The manifest of a gold folder holds a `key_check` value, which lets someone test a guessed key; it is not the key.
- **How it is rotated.** Make a new key and build a new gold folder with it. A build writes only into a folder that does not exist or is empty, so it never changes or deletes an earlier folder: you remove the old ones yourself. With a new key every `pk_`, `ak_` and `kc_` value changes, and so does the name of every `person/<key>/` folder. Old and new folders no longer join, and a person's old folder name cannot be recomputed from the new key. Deliver fresh folders to every audience, and delete the old folders and their copies if the old pseudonyms must go.
- **If it leaks,** rotate it as above. Folders already built with the old key stay linkable to persons for anyone who has the old key and the ids, so delete them and their copies.
