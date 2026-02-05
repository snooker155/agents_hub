import React, { useState, useEffect } from 'react'
import axios from 'axios'
import TaskBoard from './components/TaskBoard'
import LogPanel from './components/LogPanel'
import AgentControls from './components/AgentControls'
import InteractionForm from './components/InteractionForm'
import FlowGraph from './components/FlowGraph'
import ProjectTreeView from './components/ProjectTreeView'

function App() {
  const [stats, setStats] = useState({ total: 0, todo: 0, done: 0 })

  useEffect(() => {
    const fetchStats = async () => {
      try {
        const res = await axios.get('/api/stats')
        setStats(res.data)
      } catch (e) {
        console.error("Failed to fetch stats", e)
      }
    }
    fetchStats()
    const interval = setInterval(fetchStats, 5000)
    return () => clearInterval(interval)
  }, [])

  return (
    <div style={{ fontFamily: 'sans-serif', padding: '20px' }}>
      <h1>Agent Factory Dashboard</h1>
      <FlowGraph />

      <div style={{ display: 'flex', gap: '20px', marginBottom: '20px' }}>
        <div style={{ padding: '10px', background: '#eee', borderRadius: '5px' }}>
          <strong>Total Tasks:</strong> {stats.total}
        </div>
        <div style={{ padding: '10px', background: '#fff3cd', borderRadius: '5px' }}>
          <strong>Todo:</strong> {stats.todo}
        </div>
        <div style={{ padding: '10px', background: '#d1e7dd', borderRadius: '5px' }}>
          <strong>Done:</strong> {stats.done}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
        <div>
          <ProjectTreeView />
          <AgentControls />
        </div>
        <div>
          <InteractionForm />
          <TaskBoard />
          <LogPanel />
        </div>
      </div>
    </div>
  )
}

export default App
