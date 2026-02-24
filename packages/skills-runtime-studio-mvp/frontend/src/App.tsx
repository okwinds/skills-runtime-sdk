import { useCallback, useEffect, useMemo, useState } from 'react';
import './App.css';
import { FilmStripSidebar } from './components/layout/FilmStripSidebar';
import { SkillList } from './components/skills/SkillList';
import { SkillCreateForm } from './components/skills/SkillCreateForm';
import { SSETimeline } from './components/run/SSETimeline';
import { Tabs } from './components/ui/Tabs';
import { Button } from './components/ui/Button';
import { Textarea } from './components/ui/Input';
import {
  createSession,
  deleteSession,
  getSessionSkills,
  listSessions,
  setSessionSkillSources,
  type Session,
  type SkillManifest,
} from './lib/api';

function parseSourcesInput(text: string): string[] {
  return text
    .split(/[\n,]/g)
    .map((s) => s.trim())
    .filter(Boolean);
}

export default function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [skills, setSkills] = useState<SkillManifest[]>([]);
  const [skillsLoading, setSkillsLoading] = useState(false);
  const [sourcesInput, setSourcesInput] = useState('');
  const [sourcesSaving, setSourcesSaving] = useState(false);
  type TabId = 'skills' | 'create' | 'run';
  const [activeTab, setActiveTab] = useState<TabId>('skills');

  const setActiveTabFromString = useCallback((id: string) => {
    if (id === 'skills' || id === 'create' || id === 'run') setActiveTab(id);
  }, []);

  const activeSession = useMemo(
    () => sessions.find((s) => s.id === activeSessionId) ?? null,
    [sessions, activeSessionId],
  );

  const refreshSessions = useCallback(async () => {
    const list = await listSessions();
    setSessions(list);
    return list;
  }, []);

  const refreshSessionSkills = useCallback(async () => {
    if (!activeSessionId) return;
      setSkillsLoading(true);
    try {
      const data = await getSessionSkills(activeSessionId);
      setSkills(data.skills);
      setSourcesInput(data.filesystemSources.join('\n'));
    } finally {
      setSkillsLoading(false);
    }
  }, [activeSessionId]);

  const applySources = useCallback(async () => {
    if (!activeSessionId) return;
    const sources = parseSourcesInput(sourcesInput);
    setSourcesSaving(true);
    try {
      await setSessionSkillSources(activeSessionId, sources);
      await refreshSessionSkills();
    } finally {
      setSourcesSaving(false);
    }
  }, [activeSessionId, refreshSessionSkills, sourcesInput]);

  const createNewSession = useCallback(async () => {
    const session = await createSession();
    const list = await refreshSessions();
    if (list.length === 0) setSessions([session]);
    setActiveSessionId(session.id);
    setActiveTab('skills');
  }, [refreshSessions]);

  const handleDeleteSession = useCallback(async (sessionId: string) => {
    await deleteSession(sessionId);
    const updatedSessions = await refreshSessions();

    // If we deleted the active session, select a new one
    if (sessionId === activeSessionId) {
      if (updatedSessions.length > 0) {
        // Select the first available session
        setActiveSessionId(updatedSessions[0].id);
      } else {
        // No sessions left, create a new one
        const newSession = await createSession();
        const refreshed = await refreshSessions();
        if (refreshed.length === 0) setSessions([newSession]);
        setActiveSessionId(newSession.id);
      }
    }
  }, [activeSessionId, refreshSessions]);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      const list = await refreshSessions();
      if (cancelled) return;

      if (list.length === 0) {
        const created = await createSession();
        if (cancelled) return;
        const refreshed = await refreshSessions();
        if (cancelled) return;
        if (refreshed.length === 0) setSessions([created]);
        setActiveSessionId(created.id);
        return;
      }

      setActiveSessionId((prev) => {
        if (prev && list.some((s) => s.id === prev)) return prev;
        return list[0]?.id ?? null;
      });
    })();

    return () => {
      cancelled = true;
    };
  }, [refreshSessions]);

  useEffect(() => {
    void refreshSessionSkills();
  }, [refreshSessionSkills]);

  return (
    <div className="app-shell">
      <FilmStripSidebar
        sessions={sessions}
        activeSessionId={activeSessionId}
        onSelectSession={(id) => {
          setActiveSessionId(id);
          setActiveTab('skills');
        }}
        onCreateSession={() => {
          void createNewSession();
        }}
        onDeleteSession={handleDeleteSession}
      />

      <main className="app-main">
        {activeSession ? (
          <Tabs defaultTab="skills" activeTab={activeTab} onChange={setActiveTabFromString}>
            <Tabs.List>
              <Tabs.Tab
                id="skills"
                icon={
                  <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                    <path d="M12 2L2 7l10 5 10-5-10-5zM2 17l10 5 10-5M2 12l10 5 10-5" />
                  </svg>
                }
              >
                Skills
              </Tabs.Tab>
              <Tabs.Tab
                id="create"
                icon={
                  <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                    <path d="M12 5v14m-7-7h14" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
                  </svg>
                }
              >
                Create
              </Tabs.Tab>
              <Tabs.Tab
                id="run"
                icon={
                  <svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor">
                    <path d="M8 5v14l11-7z" />
                  </svg>
                }
              >
                Run
              </Tabs.Tab>
            </Tabs.List>

            <Tabs.Content>
              <Tabs.Panel id="skills">
                <div className="panel">
                  <div className="panel__header">
                    <div className="panel__header-title">
                      <h2>Session</h2>
                      <span className="panel__mono">{activeSession.id}</span>
                    </div>
                    <div className="panel__header-actions">
                      <Button
                        variant="secondary"
                        size="small"
                        onClick={() => void refreshSessionSkills()}
                        isLoading={skillsLoading}
                      >
                        Refresh
                      </Button>
                    </div>
                  </div>

                  <div className="panel__section">
                    <div className="panel__section-title">技能 Sources（filesystem）</div>
                    <div className="panel__roots">
                      <Textarea
                        value={sourcesInput}
                        onChange={(e) => setSourcesInput(e.target.value)}
                        rows={2}
                        placeholder="每行一个 filesystem source root（目录路径）"
                      />
                      <div className="panel__roots-actions">
                        <Button
                          variant="primary"
                          size="small"
                          onClick={() => void applySources()}
                          isLoading={sourcesSaving}
                          disabled={sourcesSaving}
                        >
                          保存 Sources
                        </Button>
                      </div>
                    </div>
                  </div>

                  <div className="panel__section panel__section--grow">
                    <SkillList skills={skills} isLoading={skillsLoading} />
                  </div>
                </div>
              </Tabs.Panel>

              <Tabs.Panel id="create">
                <div className="panel panel--scroll">
                  <SkillCreateForm
                    sessionId={activeSession.id}
                    onSuccess={() => {
                      setActiveTab('skills');
                      void refreshSessionSkills();
                    }}
                    onCancel={() => setActiveTab('skills')}
                  />
                </div>
              </Tabs.Panel>

              <Tabs.Panel id="run">
                <div className="panel panel--scroll">
                  <SSETimeline sessionId={activeSession.id} />
                </div>
              </Tabs.Panel>
            </Tabs.Content>
          </Tabs>
        ) : (
          <div className="empty-state">
            <h2>No Session</h2>
            <p>Create a new session to start.</p>
            <Button variant="primary" onClick={() => void createNewSession()}>
              Create Session
            </Button>
          </div>
        )}
      </main>
    </div>
  );
}
