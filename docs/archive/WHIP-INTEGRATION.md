# Whip Integration — Live Proof

## Resolved Endpoint

Identity: http://127.0.0.1:8088/v1
Model alias: Qwen3.6-35B-A3B

## Transcript
cd /home/svend/harness-allocator && whip -m Qwen3.6-35B-A3B "What is Qwen3.6-35B-A3B
?"
svend@svend-MS-7D75:~/DPMtF-WebUI$ cd /home/svend/harness-allocator && whip -m Qwen3
.6-35B-A3B "What is Qwen3.6-35B-A3B?"























 whip · Qwen3.6-35B-A3B @ model-allocator · /home/svend/harness… ◌ on  ⚡ off
`ctrl+p` commands

  pipeline
  • Conversational/dialogue — fine-tuned with a chat template that supports
  system prompts, tool/function calls (via XML-style tags), and reasoning chains
  (think/reason blocks using `

◌ ...

● `)

  • Code generation — includes  <parameter>  tags for structured function/tool
  calling
  • Multilingual — pre-trained on English and Chinese (zh) with multilingual
  support
  • Efficient inference — the MoE design activates only a small subset of
  experts per token, making it fast despite the 35B total parameter count
  • Licensed under Apache 2.0, making it commercially friendly

  In short, it's a compact, highly efficient vision-language model ideal for
  resource-constrained deployments where you need multimodal capabilities and
  strong conversational reasoning without the compute burden of dense 70B+
  models.

┃ Ask whip anything… (/ for commands, tab completes)

 …locator   Qwen3.6-35B-A3B   model-allocator   101.8k/891 tok · last 34.0k/428 …
cd /home/svend/harness-allocator && whip -m Qwen3.6-35B-A3B "Test"
svend@svend-MS-7D75:~/DPMtF-WebUI$ cd /home/svend/harness-allocator && whip -m Qwen3
.6-35B-A3B "Test"
























 whip · Qwen3.6-35B-A3B @ model-allocator · /home/svend/harness… ◌ on  ⚡ off
`ctrl+p` commands

compaction model: no API key — using current model

mcp: mcp-light ◌
update available: v0.4.1 (run: whip update)

❯ List the top 3 benchmark scores of Qwen3.6-35B-A3B.

◌ The user is asking about benchmark scores for a specific AI model, Qwen3.6-35B-
A3B. This is a question about external information that I don't have built-in
knowledge about. I should search the web to find this information. Let me use the
browser to search for this.

● I'll search for the benchmark scores of Qwen3.6-35B-A3B.

⚒ browser_exec Searching for Qwen3.6-35B-A3B benchmark scores

⣽  0:06 · 6.4k tok · 0% thinking… (enter queues · /theme /mouse /effort run now · es

┃ Ask whip anything… (/ for commands, tab completes)    ⣻  0:07 · 6.4k tok · 0% thin
⢿  0:07 · 6.4k tok · 0% thinking… (enter queues · /theme /mouse /effort run now · es
[launcher.Browser]2026/08/29 19:34:28 Download: htt⡿  0:07 · 6.4k tok · 0% thinking…
⣟  0:08 · 6.4k tok · 0% thinking… (enter queues · /theme /mouse /effort run now · es
                                                   ⣯  0:08 · 6.4k tok · 0% thinking…
⣷  0:09 · 6.4k tok · 0% thinking… (enter queues · /theme /mouse /effort run now · es
                                                   ⣾  0:09 · 6.4k tok · 0% thinking…
⣾  0:10 · 6.4k tok · 0% thinking… (enter queues · /theme /mouse /effort run now · es
                                                   ⣽  0:10 · 6.4k tok · 0% thinking…
⣻  0:11 · 6.4k tok · 0% thinking… (enter queues · /theme /mouse /effort run now · es
                                                   ⢿  0:11 · 6.4k tok · 0% thinking…
⡿  0:12 · 6.4k tok · 0% thinking… (enter queues · /theme /mouse /effort run now · es
                                                   ⣟  0:12 · 6.4k tok · 0% thinking…
⣯  0:13 · 6.4k tok · 0% thinking… (enter queues · /theme /mouse /effort run now · es
                                                   ⣷  0:13 · 6.4k tok · 0% thinking…
⣾  0:14 · 6.4k tok · 0% thinking… (enter queues · /theme /mouse /effort run now · es
                                                   ⣽  0:14 · 6.4k tok · 0% thinking…
⣻  0:15 · 6.4k tok · 0% thinking… (enter queues · /theme /mouse /effort run now · es
                                                   ⢿  0:15 · 6.4k tok · 0% thinking…
⡿  0:16 · 6.4k tok · 0% thinking… (enter queues · /theme /mouse /effort run now · es
                                                   ⣟  0:16 · 6.4k tok · 0% thinking…
⢿  0:16 · 6.4k tok · 0% thinking… (enter queues · /theme /mouse /effort run now · es
  |---|-----------|-------|----------|

  | 1 | MMLU-Redux | 93.3 | Knowledge |

  | 2 | MMBench EN-DEV-v1.1 | 92.8 | Vision Language |

  | 3a | AIME 2026 (I & II) | 92.7 | STEM & Reasoning |

  | 3b | AI2D_TEST | 92.7 | Vision Language |

  Notable other high scores worth mentioning:

  • RefCOCO(avg) – 92.0 (Spatial Intelligence)
  • HMMT Feb 25 – 90.7 (Mathematics)
  • C-Eval – 90.0 (Chinese Knowledge)

  The model is a sparse mixture-of-experts (MoE) architecture with 35B total
  parameters but only 3B activated per token, making it very efficient for its
  capability tier. You can find the full benchmark table on its HuggingFace page
  https://huggingface.co/Qwen/Qwen3.6-35B-A3B or blog post
  https://qwen.ai/blog?id=qwen3.6-35b-a3b.

┃ Ask whip anything… (/ for commands, tab completes)

 …locator   Qwen3.6-35B-A3B   model-allocator   86.2k/1.4k tok · last 34.6k/780 …
## Exit Code

0

## Wall Time

2026-08-29 19:32:28 -> 2026-08-29 19:35:52 (3m 24s)
