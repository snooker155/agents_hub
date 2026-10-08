/**
 * Docs sections for installation and the command line guide.
 */
import { CorpusRefs } from './CorpusDocs';
import { Callout, CodeBlock, H2, H3, P, Rich } from './DocsPrimitives';
import { useI18n } from '../../i18n';

export function Installation() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.nav.installation')}</H2>
      <P><Rich>{t('docs.installDoc.lead')}</Rich></P>

      <H3>{t('docs.installDoc.prereqTitle')}</H3>
      <ul className="list-disc pl-5 text-sm text-gray-600 space-y-1 my-2">
        <li><Rich>{t('docs.installDoc.prereqPython')}</Rich></li>
        <li><Rich>{t('docs.installDoc.prereqNode')}</Rich></li>
        <li><Rich>{t('docs.installDoc.prereqDocker')}</Rich></li>
        <li><Rich>{t('docs.installDoc.prereqKey')}</Rich></li>
      </ul>

      <H3>{t('docs.installDoc.quickstartTitle')}</H3>
      <P><Rich>{t('docs.installDoc.quickstartLead')}</Rich></P>
      <CodeBlock label={t('docs.terminal')}>{`mkdir agents-hub && cd agents-hub
curl -fsSLO https://raw.githubusercontent.com/snooker155/agents_hub/main/deploy/quickstart/docker-compose.yml
curl -fsSL  https://raw.githubusercontent.com/snooker155/agents_hub/main/deploy/quickstart/env.example -o .env
docker compose up -d`}</CodeBlock>
      <P><Rich>{t('docs.installDoc.quickstartBody')}</Rich></P>
      <CodeBlock label={t('docs.terminal')}>{`docker compose pull && docker compose up -d`}</CodeBlock>
      <P><Rich>{t('docs.installDoc.quickstartLimits')}</Rich></P>

      <H3>{t('docs.installDoc.dockerTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`git clone https://github.com/snooker155/agents_hub.git
cd agents_hub
docker compose up --build`}</CodeBlock>
      <P><Rich>{t('docs.installDoc.dockerBody')}</Rich></P>
      <CodeBlock label={t('docs.terminal')}>{`docker compose --profile scale up --build --scale backend=3`}</CodeBlock>
      <P><Rich>{t('docs.installDoc.dockerProfiles')}</Rich></P>

      <H3>{t('docs.installDoc.installerTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`git clone https://github.com/snooker155/agents_hub.git
cd agents_hub
./install.sh`}</CodeBlock>
      <P><Rich>{t('docs.installDoc.installerBody')}</Rich></P>
      <P><Rich>{t('docs.installDoc.installerFlags')}</Rich></P>
      <CodeBlock label={t('docs.terminal')}>{`ah up`}</CodeBlock>

      <H3>{t('docs.installDoc.byHandTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`python -m venv .venv && source .venv/bin/activate
pip install -e ".[backend,agents]"
cp .env.example .env
cd dashboard/frontend && npm install && cd ../..`}</CodeBlock>
      <P><Rich>{t('docs.installDoc.byHandBody')}</Rich></P>
      <CodeBlock label={t('docs.terminal')}>{`python -m uvicorn dashboard.backend.main:app --host 0.0.0.0 --port 8000 --reload
cd dashboard/frontend && npm run dev -- --host 0.0.0.0 --port 5173`}</CodeBlock>
      <P><Rich>{t('docs.installDoc.byHandNoInstall')}</Rich></P>

      <H3>{t('docs.installDoc.configTitle')}</H3>
      <CodeBlock label=".env">{`DEFAULT_PROVIDER=openai
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-5
AGENT_EXECUTION_MODE=local`}</CodeBlock>
      <P><Rich>{t('docs.installDoc.configBody')}</Rich></P>

      <H3>{t('docs.installDoc.verifyTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`curl http://localhost:8000/api/health
ah config
ah agent list`}</CodeBlock>
      <P><Rich>{t('docs.installDoc.verifyBody')}</Rich></P>

      <H3>{t('docs.installDoc.stateTitle')}</H3>
      <P><Rich>{t('docs.installDoc.stateBody')}</Rich></P>

      <H3>{t('docs.installDoc.upgradeTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`docker compose pull && docker compose up -d      # the published images
git pull && docker compose up -d --build           # compose from a checkout
git pull && ./install.sh                           # the installer`}</CodeBlock>
      <P><Rich>{t('docs.installDoc.upgradeBody')}</Rich></P>

      <Callout tone="warn"><Rich>{t('docs.installDoc.troubleCallout')}</Rich></Callout>
      <CorpusRefs ids={['installation']} />
    </div>
  );
}

export function CliGuide() {
  const { t } = useI18n();
  return (
    <div>
      <H2>{t('docs.nav.cli')}</H2>
      <P><Rich>{t('docs.cliGuide.lead')}</Rich></P>

      <H3>{t('docs.cliGuide.reachTitle')}</H3>
      <P><Rich>{t('docs.cliGuide.reachDirect')}</Rich></P>
      <P><Rich>{t('docs.cliGuide.reachHttp')}</Rich></P>

      <H3>{t('docs.cliGuide.getTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`./install.sh          # …or, with nothing installed:
python -m cli --help`}</CodeBlock>
      <P><Rich>{t('docs.cliGuide.getBody')}</Rich></P>

      <H3>{t('docs.cliGuide.commandsTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`ah agent list
ah agent run swe_agent "fix the failing test"
ah chat main-agent
ah task create "Build a REST API" --decompose
ah task list --status todo
ah workspace list
ah instance list
ah config`}</CodeBlock>
      <P><Rich>{t('docs.cliGuide.commandsBody')}</Rich></P>

      <H3>{t('docs.cliGuide.upTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`ah up                 # API on :8000, built dashboard on :5173
ah up --dev           # backend reload + Vite dev server with HMR
ah up --rebuild       # force a fresh bundle
ah up --no-frontend   # just the API`}</CodeBlock>
      <P><Rich>{t('docs.cliGuide.upBody')}</Rich></P>

      <H3>{t('docs.cliGuide.dirsTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`cd ~/code/myapp
ah workspace init

ah workspace create dev --use
cd ~/code/myapp   && ah project add
cd ~/code/backend && ah project add
ah project get`}</CodeBlock>
      <P><Rich>{t('docs.cliGuide.dirsBody')}</Rich></P>

      <H3>{t('docs.cliGuide.selectTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`ah workspace use dev
ah workspace current
ah task create "no -w needed"
ah workspace unuse`}</CodeBlock>
      <P><Rich>{t('docs.cliGuide.selectBody')}</Rich></P>
      <P><Rich>{t('docs.cliGuide.selectCwd')}</Rich></P>

      <H3>{t('docs.cliGuide.shellTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`ah shell-init --install
exec zsh`}</CodeBlock>
      <P><Rich>{t('docs.cliGuide.shellBody')}</Rich></P>

      <H3>{t('docs.cliGuide.remoteTitle')}</H3>
      <CodeBlock label={t('docs.terminal')}>{`export AGENTS_HUB_URL=http://localhost:8000
ah agent list
ah workspace init /host/code/myapp`}</CodeBlock>
      <P><Rich>{t('docs.cliGuide.remoteBody')}</Rich></P>

      <Callout tone="tip"><Rich>{t('docs.cliGuide.tip')}</Rich></Callout>
    </div>
  );
}
