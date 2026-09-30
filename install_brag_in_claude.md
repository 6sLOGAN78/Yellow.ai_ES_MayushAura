# How to Install `/brag` in Claude Code

The `/brag` skill allows you to generate a short, shareable launch video for your project directly from Claude. It also includes `/brag-slim`, a leaner version optimized for Opus 5.5 without bundled assets.

There are a few ways to install the skill in Claude Code:

## Option 1: Using the Plugin Installer (Recommended)

The easiest way to install `/brag` is using the built-in Claude Code plugin installer:

```bash
/plugin marketplace add latent-spaces/brag
/plugin install brag@brag
```

Once installed, simply run `/brag` (or `/brag-slim`) inside any project.

## Option 2: Manual Installation (Copying directly)

If you prefer not to use the plugin installer or it is unavailable, you can manually copy the skill files into your Claude skills directory:

1. Clone the repository or download the source code.
2. Run the following commands to sync the skill folders:

```bash
rsync -a --exclude '.DS_Store' skills/brag/ ~/.claude/skills/brag/

# Optional: To also install the /brag-slim command
rsync -a --exclude '.DS_Store' skills/brag-slim/ ~/.claude/skills/brag-slim/  
```

3. **Restart Claude Code** after copying the files for the new skill to be recognized.

## Option 3: Using the `skills` CLI

If you use the universal `skills` CLI installer, you can install the skill with a single command:

```bash
# To install for the current project:
npx skills add https://github.com/latent-spaces/brag --skill brag

# To install globally (available in every project):
npx skills add https://github.com/latent-spaces/brag --skill brag -g
```

To install just `/brag-slim` using the skills CLI, use:
```bash
npx skills add https://github.com/latent-spaces/brag --skill brag-slim
```


## Resources & Video Inspiration

If you're looking for inspiration for your `/brag` launch videos, check out these examples and prompts:

1. [Skillry - Opus 5.5 AI Videos](https://skillry.dev/ai-videos/opus-5-5)
2. [Revid.ai - Claude Motion Graphics](https://www.revid.ai/claude-motion-graphics)
3. [Whatships](https://whatships.com/)
