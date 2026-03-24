import React, { useEffect, useMemo, useState } from 'react';
import { Background, Controls, Handle, MiniMap, Position, ReactFlow } from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import toast from 'react-hot-toast';
import { BookOpen, CheckCircle2, GitBranch, Target } from 'lucide-react';
import RequireUser from '../components/RequireUser';
import { useUser } from '../context/UserContext';
import { generateMindMapTopicDescription, getMindMap, listSubjects } from '../services/api';

const ALL_UNITS = '__all_units__';

async function fetchMindMapWithRetry(userId, attempts = 2) {
  let lastError;
  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      return await getMindMap(userId);
    } catch (error) {
      lastError = error;
      if (attempt < attempts - 1) {
        await new Promise((resolve) => setTimeout(resolve, 700));
      }
    }
  }
  throw lastError;
}

function normalizeFallbackMap(data, subjectRows) {
  if ((data?.subjects || []).length) return data;
  const fallbackSubjects = (subjectRows || []).map((subject) => ({
    id: subject.id,
    label: subject.name,
    color: subject.color || '#4A90D9',
    exam_date: subject.exam_date || null,
    priority: Number(subject.priority || 0),
    topic_count: 0,
    completed_topics: 0,
    weak_topic_count: 0,
    completion_pct: 0,
    snapshot: `${subject.name} is ready for topics to be added.`,
    units: [],
  }));
  return {
    ...(data || {}),
    subject_count: fallbackSubjects.length,
    topic_count: data?.topic_count || 0,
    completed_topics: data?.completed_topics || 0,
    overall_completion_pct: data?.overall_completion_pct || 0,
    subjects: fallbackSubjects,
  };
}

function pct(value) {
  return `${Math.round(Number(value || 0))}%`;
}

function statusClass(topic) {
  if (topic.is_weak) return 'weak';
  if (topic.completed) return 'done';
  if (Number(topic.completion_pct || 0) >= 60) return 'steady';
  return 'focus';
}

function statusHint(topic) {
  if (topic.is_weak) {
    return topic.weak_attempts
      ? `${topic.weak_attempts} quiz attempts below target`
      : 'Needs revision support';
  }
  if (topic.completed) return 'Completed and ready for revision';
  if (Number(topic.completion_pct || 0) >= 60) return 'Good progress, finish with practice';
  return 'Start here and build fundamentals';
}

function topicDescription(topic) {
  return topic.description || '';
}

function branchDirection(index) {
  return index % 2 === 0 ? 1 : -1;
}

function flattenTopics(units) {
  return units.flatMap((unit) =>
    (unit.topics || []).map((topic) => ({
      ...topic,
      unit_id: unit.id,
      unit_label: unit.label,
    })),
  );
}

function buildGraph(subject, units) {
  if (!subject) return { nodes: [], edges: [] };

  const accent = subject.color || '#4A90D9';
  const rootId = `subject-${subject.id}`;
  const allTopics = flattenTopics(units);
  const nodes = [
    {
      id: rootId,
      type: 'mindMapNode',
      position: { x: 0, y: 0 },
      draggable: false,
      selectable: false,
      data: {
        variant: 'subject',
        kind: 'subject',
        direction: 1,
        label: subject.label,
        meta: `${allTopics.length} topics`,
        description: subject.snapshot || '',
        payload: subject,
      },
    },
  ];
  const edges = [];
  const unitGapY = 180;
  const unitOffsetX = 320;
  const topicGapY = 106;
  const topicOffsetX = 300;

  const orderedUnits = [...units].sort((a, b) =>
    String(a.label || '').localeCompare(String(b.label || ''), undefined, {
      numeric: true,
      sensitivity: 'base',
    }),
  );

  const centeredUnitBase = ((Math.max(orderedUnits.length, 1) - 1) * unitGapY) / 2;

  orderedUnits.forEach((unit, index) => {
    const direction = branchDirection(index);
    const unitId = `unit-${unit.id}`;
    const unitY = index * unitGapY - centeredUnitBase;
    const orderedTopics = [...(unit.topics || [])].sort((a, b) => {
      const left = Number(a.order_index || 0);
      const right = Number(b.order_index || 0);
      if (left !== right) return left - right;
      return String(a.label || '').localeCompare(String(b.label || ''));
    });

    nodes.push({
      id: unitId,
      type: 'mindMapNode',
      position: { x: unitOffsetX * direction, y: unitY },
      draggable: false,
      selectable: false,
      data: {
        variant: 'unit',
        kind: 'unit',
        direction,
        label: unit.label,
        meta: `${orderedTopics.length} topics`,
        description: unit.headline || '',
        payload: unit,
      },
    });

    edges.push({
      id: `${rootId}-${unitId}`,
      source: rootId,
      target: unitId,
      type: 'smoothstep',
      style: {
        stroke: accent,
        strokeWidth: 2.4,
      },
    });

    const centeredTopicBase = ((Math.max(orderedTopics.length, 1) - 1) * topicGapY) / 2;

    orderedTopics.forEach((topic, topicIndex) => {
      const topicId = `topic-${topic.id}`;
      const topicY = unitY + topicIndex * topicGapY - centeredTopicBase;

      nodes.push({
        id: topicId,
        type: 'mindMapNode',
        position: { x: direction * (unitOffsetX + topicOffsetX), y: topicY },
        draggable: false,
        selectable: false,
        data: {
          variant: statusClass(topic),
          kind: 'topic',
          direction,
          label: topic.label,
          meta: unit.label,
          description: topicDescription(topic),
          payload: {
            ...topic,
            unit_label: unit.label,
          },
        },
      });

      edges.push({
        id: `${unitId}-${topicId}`,
        source: unitId,
        target: topicId,
        type: 'smoothstep',
        animated: topic.is_weak,
        style: {
          stroke: topic.is_weak
            ? '#dc2626'
            : topic.completed
            ? '#0ba26c'
            : Number(topic.completion_pct || 0) >= 60
            ? '#2563eb'
            : accent,
          strokeWidth: 2.1,
        },
      });
    });
  });

  return { nodes, edges };
}

function MindMapNode({ data }) {
  const targetPosition = data.direction === -1 ? Position.Right : Position.Left;
  const sourcePosition = data.direction === -1 ? Position.Left : Position.Right;

  return (
    <div className={`mind-map-node-content ${data.variant || ''}`}>
      <Handle type="target" position={targetPosition} className="mind-map-handle" />
      <span>{data.meta}</span>
      <strong>{data.label}</strong>
      {data.description ? <p>{data.description}</p> : null}
      <Handle type="source" position={sourcePosition} className="mind-map-handle" />
    </div>
  );
}

const nodeTypes = { mindMapNode: MindMapNode };

export default function MindMap() {
  const { userId } = useUser();
  const [map, setMap] = useState(null);
  const [subjectId, setSubjectId] = useState('');
  const [unitId, setUnitId] = useState(ALL_UNITS);
  const [loading, setLoading] = useState(true);
  const [activePanel, setActivePanel] = useState(null);
  const [panelLoading, setPanelLoading] = useState(false);
  const [panelError, setPanelError] = useState('');

  useEffect(() => {
    if (!userId) return undefined;

    let alive = true;
    const load = async () => {
      setLoading(true);
      try {
        const [mindMapResult, subjectsResult] = await Promise.allSettled([
          fetchMindMapWithRetry(userId, 2),
          listSubjects(userId),
        ]);
        if (!alive) return;

        const subjectRows =
          subjectsResult.status === 'fulfilled' ? subjectsResult.value.data || [] : [];
        const mindMapData =
          mindMapResult.status === 'fulfilled' ? mindMapResult.value.data || null : null;

        const normalizedMap = normalizeFallbackMap(mindMapData, subjectRows);
        const subjects = normalizedMap.subjects || [];
        setMap(normalizedMap);
        setSubjectId(subjects[0]?.id || '');
        setUnitId(ALL_UNITS);
        setActivePanel(null);
        setPanelError('');

        if (
          mindMapResult.status === 'rejected' &&
          (subjectsResult.status !== 'fulfilled' || !subjectRows.length)
        ) {
          throw mindMapResult.reason;
        }

        if (mindMapResult.status === 'rejected' && subjectRows.length) {
          toast.error('Loaded subjects, but the mind map details could not be refreshed right now.');
        }
      } catch (err) {
        if (!alive) return;
        setMap({ subjects: [] });
        toast.error(err.response?.data?.detail || 'Could not load the mind map');
      } finally {
        if (alive) setLoading(false);
      }
    };

    load();
    return () => {
      alive = false;
    };
  }, [userId]);

  const subjects = map?.subjects || [];
  const selectedSubject = useMemo(
    () => subjects.find((subject) => subject.id === subjectId) || subjects[0] || null,
    [subjects, subjectId],
  );

  const availableUnits = useMemo(() => selectedSubject?.units || [], [selectedSubject]);

  const visibleUnits = useMemo(() => {
    if (unitId === ALL_UNITS) return availableUnits;
    return availableUnits.filter((unit) => unit.id === unitId);
  }, [availableUnits, unitId]);

  const subjectTopics = useMemo(() => flattenTopics(visibleUnits), [visibleUnits]);

  const statCards = map
    ? [
        { label: 'Subjects', value: map.subject_count, icon: <BookOpen size={18} /> },
        { label: 'Topics', value: map.topic_count, icon: <GitBranch size={18} /> },
        { label: 'Completed', value: map.completed_topics, icon: <CheckCircle2 size={18} /> },
        { label: 'Progress', value: pct(map.overall_completion_pct), icon: <Target size={18} /> },
      ]
    : [];

  const focusIdeas = useMemo(
    () => [...new Set(visibleUnits.flatMap((unit) => unit.focus_points || []))].slice(0, 8),
    [visibleUnits],
  );

  const flowGraph = useMemo(() => buildGraph(selectedSubject, visibleUnits), [selectedSubject, visibleUnits]);

  useEffect(() => {
    setActivePanel(null);
    setPanelLoading(false);
    setPanelError('');
  }, [subjectId, unitId]);

  const activeTopic = activePanel?.kind === 'topic' ? activePanel.payload : null;

  const handleNodeClick = async (_, node) => {
    setActivePanel(node.data);
    setPanelError('');
    if (node.data?.kind !== 'topic') {
      setPanelLoading(false);
      return;
    }

    const payload = node.data.payload || {};
    if (payload.description) {
      setPanelLoading(false);
      return;
    }

    setPanelLoading(true);
    try {
      const { data } = await generateMindMapTopicDescription({
        subject_name: selectedSubject?.label || '',
        unit_name: payload.unit_label || '',
        topic_label: payload.label || '',
      });
      const description = String(data?.description || '').trim();
      if (!description) return;

      setMap((current) => {
        if (!current) return current;
        return {
          ...current,
          subjects: (current.subjects || []).map((subject) =>
            subject.id !== selectedSubject?.id
              ? subject
              : {
                  ...subject,
                  units: (subject.units || []).map((unit) =>
                    unit.label !== payload.unit_label
                      ? unit
                      : {
                          ...unit,
                          topics: (unit.topics || []).map((topic) =>
                            topic.id === payload.id ? { ...topic, description } : topic,
                          ),
                        },
                  ),
                },
          ),
        };
      });

      setActivePanel((current) =>
        current?.kind === 'topic'
          ? {
              ...current,
              payload: {
                ...current.payload,
                description,
              },
              description,
            }
          : current,
      );
    } catch (error) {
      const detail = error.response?.data?.detail || 'Groq could not generate a topic overview right now.';
      setPanelError(detail);
      toast.error(detail);
    } finally {
      setPanelLoading(false);
    }
  };

  return (
    <RequireUser>
      <div className="page mind-map-page">
        <section className="mind-map-header">
          <div>
            <span className="mind-map-kicker">Quick understanding map</span>
            <h1 className="page-title">Mind Map</h1>
            <p className="mind-map-copy">A visual map built directly from each subject and its topics.</p>
          </div>
          <div className="mind-map-stats">
            {statCards.map((card) => (
              <article key={card.label} className="mind-map-stat">
                <span>{card.icon}</span>
                <div>
                  <strong>{card.value}</strong>
                  <small>{card.label}</small>
                </div>
              </article>
            ))}
          </div>
        </section>

        <section className="mind-map-stage card">
          <div className="mind-map-toolbar">
            <div>
              <strong>Interactive subject-topic map</strong>
              <p>Choose a subject or narrow it to a single unit.</p>
            </div>
            <div className="mind-map-selectors">
              <label className="mind-map-filter">
                <span>Subject</span>
                <select
                  value={selectedSubject?.id || ''}
                  onChange={(e) => {
                    setSubjectId(e.target.value);
                    setUnitId(ALL_UNITS);
                  }}
                >
                  {subjects.map((subject) => (
                    <option key={subject.id} value={subject.id}>
                      {subject.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className="mind-map-filter">
                <span>Unit</span>
                <select value={unitId} onChange={(e) => setUnitId(e.target.value)}>
                  <option value={ALL_UNITS}>All units</option>
                  {availableUnits.map((unit) => (
                    <option key={unit.id} value={unit.id}>
                      {unit.label}
                    </option>
                  ))}
                </select>
              </label>
            </div>
          </div>

          {loading ? (
            <div className="mind-map-empty">Loading your map...</div>
          ) : !selectedSubject ? (
            <div className="mind-map-empty">No subject or topic data found yet. Add syllabus topics first.</div>
          ) : !subjectTopics.length ? (
            <>
              <div className="mind-map-overview">
                <article className="mind-map-overview-card">
                  <span>Subject</span>
                  <strong>{selectedSubject.label}</strong>
                </article>
                <article className="mind-map-overview-card">
                  <span>Status</span>
                  <strong>No topics added yet</strong>
                </article>
                <article className="mind-map-overview-card">
                  <span>Exam date</span>
                  <strong>{selectedSubject.exam_date || 'Not set'}</strong>
                </article>
                <article className="mind-map-overview-card">
                  <span>Priority</span>
                  <strong>{selectedSubject.priority || 0}</strong>
                </article>
              </div>
              <div className="mind-map-empty">
                This subject is available now. Add topics or import a syllabus to turn it into a full mind map.
              </div>
            </>
          ) : (
            <>
              <div className="mind-map-overview">
                <article className="mind-map-overview-card">
                  <span>Subject</span>
                  <strong>{selectedSubject.label}</strong>
                </article>
                <article className="mind-map-overview-card">
                  <span>Key ideas</span>
                  <strong>{focusIdeas.length ? focusIdeas.join(' • ') : 'Focus ideas will appear once topics are available.'}</strong>
                </article>
                <article className="mind-map-overview-card">
                  <span>Coverage</span>
                  <strong>{subjectTopics.filter((topic) => topic.completed).length}/{subjectTopics.length}</strong>
                </article>
                <article className="mind-map-overview-card">
                  <span>Weak areas</span>
                  <strong>{subjectTopics.filter((topic) => topic.is_weak).length}</strong>
                </article>
              </div>

              <div className="mind-map-legend">
                <span><i className="is-subject" /> Subject</span>
                <span><i className="is-focus" /> Needs focus</span>
                <span><i className="is-steady" /> Strong progress</span>
                <span><i className="is-done" /> Done</span>
              </div>

              <div className="mind-map-layout">
                <div className="mind-map-flow">
                  <ReactFlow
                    nodes={flowGraph.nodes}
                    edges={flowGraph.edges}
                    nodeTypes={nodeTypes}
                    fitView
                    fitViewOptions={{ padding: 0.2 }}
                    minZoom={0.35}
                    maxZoom={1.5}
                    nodesDraggable={false}
                    nodesConnectable={false}
                    elementsSelectable
                    onNodeClick={handleNodeClick}
                    proOptions={{ hideAttribution: true }}
                  >
                    <MiniMap pannable zoomable />
                    <Controls showInteractive={false} />
                    <Background gap={20} size={1} color="rgba(148, 163, 184, 0.28)" />
                  </ReactFlow>
                </div>

                <aside className="mind-map-sidecard">
                  {activeTopic ? (
                    <>
                      <span className="mind-map-sidecard-kicker">{activeTopic.unit_label}</span>
                      <h3>{activeTopic.label}</h3>
                      <p>
                        {panelLoading
                          ? 'Generating a short topic overview with Groq...'
                          : activeTopic.description || panelError || 'Select the topic again to request a Groq overview.'}
                      </p>
                    </>
                  ) : (
                    <>
                      <span className="mind-map-sidecard-kicker">Topic details</span>
                      <h3>Click a topic node</h3>
                      <p>
                        Select any topic in the map to see its small description and topic type on this side panel.
                      </p>
                    </>
                  )}
                </aside>
              </div>
            </>
          )}
        </section>
      </div>
    </RequireUser>
  );
}

