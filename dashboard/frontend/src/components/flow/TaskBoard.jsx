import { useI18n } from '../../i18n';
import React, { useState, useEffect } from 'react'
import axios from 'axios'
import { useLiveRefetch } from '../stream';

function TaskBoard() {
  const { t } = useI18n();
  const [tasks, setTasks] = useState([])

  const fetchTasks = () => {
    axios.get('/api/tasks')
      .then((res) => setTasks(res.data))
      .catch((e) => console.error("Failed to fetch tasks", e))
  }

  useEffect(() => { fetchTasks() }, [])
  useLiveRefetch(fetchTasks, { type: 'tasks.changed' })

  return (
    <div>
      <h2>{t('flowTaskBoard.taskBoard')}</h2>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ background: 'var(--surface-raised)', borderBottom: '2px solid var(--border-default)' }}>
            <th style={{ textAlign: 'left', padding: '10px' }}>ID</th>
            <th style={{ textAlign: 'left', padding: '10px' }}>{t('flowTaskBoard.title')}</th>
            <th style={{ textAlign: 'left', padding: '10px' }}>{t('flowTaskBoard.assignee')}</th>
            <th style={{ textAlign: 'left', padding: '10px' }}>{t('flowTaskBoard.status')}</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map(task => (
            <tr key={task.id} style={{ borderBottom: '1px solid var(--border-default)' }}>
              <td style={{ padding: '10px' }}>{task.id}</td>
              <td style={{ padding: '10px' }}>{task.title}</td>
              <td style={{ padding: '10px' }}>{task.assignee}</td>
              <td style={{ padding: '10px' }}>
                <span style={{
                  padding: '4px 8px',
                  borderRadius: '12px',
                  fontSize: '0.85em',
                  background: task.status === 'Done' ? 'var(--ok-surface)' : (task.status === 'InProgress' ? 'var(--info-surface)' : 'var(--surface-raised)')
                }}>
                  {task.status}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export default TaskBoard
