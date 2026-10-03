import { useEffect, useState } from 'react';
import { Button, Input, Modal, Popconfirm, Alert, App } from 'antd';
import { ArrowRight, ChatsCircle, MagnifyingGlass, PencilSimple, Plus, Trash } from '@phosphor-icons/react';
import { useTranslation } from 'react-i18next';
import HeaderControls from '../../../components/HeaderControls';
import * as api from '../../../services/api';
import type { Conversation, Project, ProjectSearchResult } from '../../../types';
import styles from './ProjectOverview.module.css';

type Props = {
  projectId: string;
  fallbackProject?: Project;
  conversations: Conversation[];
  onOpenConversation: (conversationId: string) => void;
  onNewConversation: () => void;
  onChanged: () => void;
  onDeleted: () => void;
};

function errorText(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback;
}

export default function ProjectOverview({ projectId, fallbackProject, conversations, onOpenConversation, onNewConversation, onChanged, onDeleted }: Props) {
  const { t } = useTranslation();
  const { message } = App.useApp();
  const [project, setProject] = useState<Project | null>(null);
  const [brief, setBrief] = useState('');
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState('');
  const [saving, setSaving] = useState(false);
  const [query, setQuery] = useState('');
  const [results, setResults] = useState<ProjectSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState('');
  const [renameOpen, setRenameOpen] = useState(false);
  const [newName, setNewName] = useState('');
  const [renaming, setRenaming] = useState(false);
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setProject(null);
    setBrief('');
    setQuery('');
    setResults([]);
    setLoading(true);
    setLoadError('');
    api.getProject(projectId).then(value => {
      if (cancelled) return;
      setProject(value);
      setBrief(value.brief || '');
    }).catch(error => {
      if (!cancelled) setLoadError(errorText(error, t('projects.loadFailed')));
    }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [projectId, t]);

  useEffect(() => {
    const normalized = query.trim();
    if (!normalized) {
      setResults([]);
      setSearching(false);
      setSearchError('');
      return;
    }
    let cancelled = false;
    setResults([]);
    setSearching(true);
    setSearchError('');
    const timer = window.setTimeout(() => {
      api.searchProject(projectId, normalized).then(value => {
        if (!cancelled) setResults(value.results);
      }).catch(error => {
        if (!cancelled) setSearchError(errorText(error, t('projects.searchFailed')));
      }).finally(() => { if (!cancelled) setSearching(false); });
    }, 250);
    return () => { cancelled = true; window.clearTimeout(timer); };
  }, [projectId, query, t]);

  async function saveBrief() {
    if (!project || saving) return;
    const submitted = brief;
    setSaving(true);
    try {
      const updated = await api.saveProjectBrief(projectId, submitted);
      setProject(updated);
      setBrief(current => current === submitted ? updated.brief || '' : current);
      onChanged();
      message.success(t('projects.briefSaved'));
    } catch (error) {
      message.error(errorText(error, t('common.saveFailed')));
    } finally {
      setSaving(false);
    }
  }

  async function rename() {
    const name = newName.trim();
    if (!name || renaming) return;
    setRenaming(true);
    try {
      const updated = await api.renameProject(projectId, name);
      setProject(updated);
      setRenameOpen(false);
      onChanged();
    } catch (error) {
      message.error(errorText(error, t('projects.renameFailed')));
    } finally {
      setRenaming(false);
    }
  }

  async function remove() {
    if (deleting) return;
    setDeleting(true);
    try {
      await api.deleteProject(projectId);
      message.success(t('projects.deleted'));
      onDeleted();
    } catch (error) {
      message.error(errorText(error, t('projects.deleteFailed')));
    } finally {
      setDeleting(false);
    }
  }

  const active = project || fallbackProject;
  const dirty = project !== null && brief !== (project.brief || '');
  const draftBytes = new TextEncoder().encode(brief).length;
  const visibleConversations = conversations.filter(conv => conv.project_id === projectId);

  return <>
    <div className={styles.header}>
      <span className={styles.headerTitle}>{active?.name || t('projects.title')}</span>
      <HeaderControls />
    </div>
    <div className={styles.scroll}>
      <div className={styles.content}>
        <div className={styles.eyebrow}>{t('projects.title')}</div>
        <div className={styles.titleRow}>
          <div>
            <h1>{active?.name || t('projects.title')}</h1>
            <p>{t('projects.description')}</p>
          </div>
          <div className={styles.actions}>
            <Button icon={<PencilSimple size={16} />} onClick={() => { setNewName(active?.name || ''); setRenameOpen(true); }} disabled={!project}>{t('common.rename')}</Button>
            <Popconfirm title={t('projects.deleteTitle')} description={t('projects.deleteDescription')}
              okText={t('common.delete')} cancelText={t('common.cancel')} okButtonProps={{ danger: true }} onConfirm={() => void remove()}>
              <Button danger loading={deleting} icon={<Trash size={16} />} disabled={!project}>{t('common.delete')}</Button>
            </Popconfirm>
          </div>
        </div>

        {loadError && <Alert type="error" showIcon message={loadError} action={<Button size="small" onClick={() => { setLoading(true); setLoadError(''); void api.getProject(projectId).then(value => { setProject(value); setBrief(value.brief || ''); }).catch(error => setLoadError(errorText(error, t('projects.loadFailed')))).finally(() => setLoading(false)); }}>{t('common.refresh')}</Button>} />}
        <section className={styles.card} aria-label={t('projects.brief')}>
          <div className={styles.sectionHeading}>
            <div>
              <h2>{t('projects.brief')}</h2>
              <p>{t('projects.briefHint')}</p>
            </div>
            <span className={styles.budget}>{t('projects.contextBudget')}: {project?.brief_token_count ?? 0} / 5000{dirty ? ` · ${t('projects.draftBytes', { count: draftBytes })}` : ''}</span>
          </div>
          {project?.brief_truncated && <Alert className={styles.warning} type="warning" showIcon message={t('projects.briefTruncated')} />}
          {dirty && draftBytes >= 4800 && <Alert className={styles.warning} type="warning" showIcon message={t('projects.draftMayTruncate')} />}
          <Input.TextArea
            className={styles.briefInput}
            value={brief}
            onChange={event => setBrief(event.target.value)}
            placeholder={t('projects.briefPlaceholder')}
            aria-label={t('projects.brief')}
            autoSize={{ minRows: 7, maxRows: 18 }}
            disabled={loading || !project}
          />
          <div className={styles.briefFooter}>
            <span>{dirty ? t('common.unsaved') : t('projects.briefFile')}</span>
            <Button type="primary" onClick={() => void saveBrief()} loading={saving} disabled={!dirty || loading}>{t('common.save')}</Button>
          </div>
        </section>

        <section className={styles.conversations} aria-label={t('projects.relatedConversations')}>
          <div className={styles.sectionHeading}>
            <div>
              <h2>{t('projects.relatedConversations')}</h2>
              <p>{t('projects.conversationCount', { count: visibleConversations.length })}</p>
            </div>
            <Button icon={<Plus size={16} />} onClick={onNewConversation}>{t('projects.newConversation')}</Button>
          </div>
          <Input
            value={query}
            onChange={event => setQuery(event.target.value)}
            allowClear
            prefix={<MagnifyingGlass size={16} />}
            placeholder={t('projects.searchPlaceholder')}
            aria-label={t('projects.searchPlaceholder')}
          />
          {searchError && <Alert className={styles.warning} type="error" showIcon message={searchError} />}
          <div className={styles.results} aria-busy={searching}>
            {query.trim() ? (searching && !results.length ? <div className={styles.empty}>{t('common.loading')}</div>
              : results.length ? results.map((result, index) => {
                return <button key={`${result.conversation_id}-${result.message_index}-${index}`} type="button" className={styles.result}
                  onClick={() => onOpenConversation(result.conversation_id)}>
                  <span className={styles.resultIcon}><ChatsCircle size={18} /></span>
                  <span className={styles.resultText}><strong>{result.title}</strong><small>{result.snippet}</small></span>
                  <ArrowRight size={16} className={styles.resultArrow} />
                </button>;
              }) : <div className={styles.empty}>{t('projects.noResults')}</div>)
              : visibleConversations.length ? visibleConversations.map(conversation => <button key={conversation.id} type="button" className={styles.result}
                onClick={() => onOpenConversation(conversation.id)}>
                <span className={styles.resultIcon}><ChatsCircle size={18} /></span>
                <span className={styles.resultText}><strong>{conversation.title}</strong></span>
                <ArrowRight size={16} className={styles.resultArrow} />
              </button>) : <div className={styles.empty}>{t('projects.emptyConversations')}</div>}
          </div>
        </section>
      </div>
    </div>
    <Modal title={t('projects.rename')} open={renameOpen} onOk={() => void rename()} onCancel={() => setRenameOpen(false)}
      confirmLoading={renaming} okButtonProps={{ disabled: !newName.trim() }} okText={t('common.save')} cancelText={t('common.cancel')}>
      <Input value={newName} onChange={event => setNewName(event.target.value)} onPressEnter={() => void rename()} maxLength={80} autoFocus />
    </Modal>
  </>;
}
