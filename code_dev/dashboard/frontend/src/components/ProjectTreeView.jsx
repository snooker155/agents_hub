import React, { useState, useEffect } from 'react'
import axios from 'axios'

function ProjectTreeView() {
  const [tasks, setTasks] = useState([])
  const [artifacts, setArtifacts] = useState([])

  const fetchData = async () => {
    try {
      const [tasksRes, artifactsRes] = await Promise.all([
        axios.get('/api/tasks'),
        axios.get('/api/artifacts')
      ])
      setTasks(tasksRes.data)
      setArtifacts(artifactsRes.data)
    } catch (e) {
      console.error("Failed to fetch project data", e)
    }
  }

  useEffect(() => {
    fetchData()
    const interval = setInterval(fetchData, 5000)
    return () => clearInterval(interval)
  }, [])

  const groupedTasks = tasks.reduce((acc, task) => {
    const assignee = task.assignee || 'Unknown'
    if (!acc[assignee]) acc[assignee] = []
    acc[assignee].push(task)
    return acc
  }, {})

  return (
    <div style={{ marginTop: '20px', padding: '15px', border: '1px solid #ddd', borderRadius: '5px' }}>
      <h3>Project Overview (High Order View)</h3>

      <div style={{ marginLeft: '10px' }}>
        <div style={{ fontWeight: 'bold', marginBottom: '10px' }}>📁 Project Documents</div>
        <ul style={{ listStyleType: 'none', paddingLeft: '20px' }}>
          {artifacts.filter(a => a.type === 'doc').map((art, i) => (
            <li key={i}>📄 {art.name} <small style={{ color: '#666' }}>({art.path})</small></li>
          ))}
        </ul>

        <div style={{ fontWeight: 'bold', margin: '15px 0 10px' }}>🛠 Development Tracks</div>
        {Object.entries(groupedTasks).map(([assignee, assigneeTasks]) => (
          <div key={assignee} style={{ marginBottom: '15px', paddingLeft: '20px' }}>
            <div style={{ textDecoration: 'underline' }}>{assignee} Agents</div>
            <ul style={{ listStyleType: 'none', paddingLeft: '20px' }}>
              {assigneeTasks.map(task => (
                <li key={task.id} style={{ marginBottom: '8px' }}>
                  <strong>{task.id}: {task.title}</strong> [{task.status}]
                  <ul style={{ listStyleType: 'none', paddingLeft: '20px', fontSize: '0.9em', color: '#444' }}>
                    {(task.artifacts || []).map((art, j) => (
                      <li key={j}>💾 {art}</li>
                    ))}
                  </ul>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </div>
  )
}

export default ProjectTreeView
