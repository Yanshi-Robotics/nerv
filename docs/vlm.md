# Visual model configuration

Open Visual models in the sidebar. The panel stores editable model registrations and write-only provider credentials. It does not make a model ready merely because a key exists.

## Initial registrations

The initial catalogue was checked against supplier documentation on 2026-10-19. These are model identifiers, not evidence that the current account can call them.

| Registration | API model | Effort choices | Protocol |
|---|---|---|---|
| DeepSeek V4.1 Flash | `deepseek-flash` | none, low, high, max | DeepSeek Responses |
| GPT-6.1 Sol | `gpt-6.1-sol` | low, medium, high, xhigh, max | OpenAI Responses |
| GPT-6 Astra | `gpt-6-astra` | low, medium, high, xhigh, max | OpenAI Responses |

[Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol) and [Astra](https://developers.openai.com/api/docs/models/gpt-6-astra) support image input, tools and reasoning effort. [DeepSeek's model table](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/) identifies Flash's image and tool support; [thinking-mode documentation](https://api-docs.deepseek.com/guides/thinking_mode/) describes its effort values. NERV omits an unspecified effort rather than substituting another level.

OpenAI tools use Responses. The adapter preserves full output items, including encrypted reasoning, with a provider identity that includes endpoint, model, protocol and effort. Changing identity discards provider-specific continuation and uses neutral text/tool history. DeepSeek excludes OpenAI-only request fields and keeps its own continuation items.

## Registration fields

A registration records supplier, endpoint, wire protocol, model ID, display label, image modalities, capability evidence, tools/streaming/continuation, effort choices, context capacity, provider output limit, configured output budget, timeout, credential reference and enabled state. Context/limit fields are null when unconfirmed. The configured output budget is a request limit, not the supplier's advertised maximum.

Only documented image-capable registrations are admitted. Running readiness additionally requires enabled tool and continuation support, a configured key and passing interface checks for the current request signature. A custom OpenAI-compatible text model cannot enter by claiming only tool support.

## Keys and validation

Keys are stored in a private mode-0600 file under `NERV_HOME`. Environment values take precedence and make the corresponding UI setting read-only. Keys are never returned by GET, placed in model history or passed to robot workers. Saving/removing one invalidates its models' checks.

The explicit validation button sends a generated visual code to a non-robot tool, checks the returned tool arguments, then submits the tool result for a continuation response. It can incur charges. Discovery, key saving, session creation and backend startup do not run it.

A check records the request signature and time. A changed model declaration, effort or key cannot reuse it. Errors expose their class, not raw provider exceptions that might contain credentials. No automatic provider fallback occurs.

## Task settings

The selected model and each model's last effort persist independently. Chat shows only the selected model's supported effort values. Active turns and executions lock model settings; changing a model does not reconnect the device.

Request timeout, robot supervision timeout and inference frame freshness are independent. A slow or failed cloud call does not extend local movement permission. Configure these based on measured latency; high reasoning effort is not a reason to disable the local watchdog.

## Physical status

The panel separately displays the operator's physical acceptance record. Its signature must match the current model, effort and key configuration. Interface validation can never award physical acceptance. Record and retain the frozen trial evidence using [the acceptance procedure](acceptance.md).
