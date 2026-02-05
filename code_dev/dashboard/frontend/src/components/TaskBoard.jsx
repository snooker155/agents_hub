import React, { useState, useEffect } from 'react'
import axios from 'axios'

function TaskBoard() {
  const [tasks, setTasks] = useState([])

  const fetchTasks = async () => {
    try {
      const res = await axios.get('/api/tasks')
      setTasks(res.data)
    } catch (e) {
      console.error("Failed to fetch tasks", e)
    }
  }

  useEffect(() => {
    fetchTasks()
    const interval = setInterval(fetchTasks, 5000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div>
      <h2>Task Board</h2>
      <table style={{ width: '100%', borderCollapse: 'collapse' }}>
        <thead>
          <tr style={{ background: '#f8f9fa', borderBottom: '2px solid #dee2e6' }}>
            <th style={{ textAlign: 'left', padding: '10px' }}>ID</th>
            <th style={{ textAlign: 'left', padding: '10px' }}>Title</th>
            <th style={{ textAlign: 'left', padding: '10px' }}>Assignee</th>
            <th style={{ textAlign: 'left', padding: '10px' }}>Status</th>
          </tr>
        </thead>
        <tbody>
          {tasks.map(task => (
            <tr key={task.id} style={{ borderBottom: '1px solid #dee2e6' }}>
              <td style={{ padding: '10px' }}>{task.id}</td>
              <td style={{ padding: '10px' }}>{task.title}</td>
              <td style={{ padding: '10px' }}>{task.assignee}</td>
              <td style={{ padding: '10px' }}>
                <span style={{
                  padding: '4px 8px',
                  borderRadius: '12px',
                  fontSize: '0.85em',
                  background: task.status === 'Done' ? '#d1e7dd' : (task.status === 'InProgress' ? '#cfe2ff' : '#f8f9fa')
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
