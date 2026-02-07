import React, { useState } from 'react'
import axios from 'axios'

function AgentControls() {
  const [description, setDescription] = useState('')

  const runAgent = async (agent, action = null) => {
    try {
      await axios.post('/api/run-agent', {
        agent,
        action,
        description: agent === 'graph' || agent === 'pm' ? description : null
      })
      alert(`Started ${agent} ${action || ''}`)
    } catch (e) {
      alert("Failed to start agent")
    }
  }

  return (
    <div style={{ marginTop: '20px', padding: '15px', border: '1px solid #ddd', borderRadius: '5px' }}>
      <h3>Agent Controls</h3>
      <div style={{ marginBottom: '15px' }}>
        <input
          type="text"
          placeholder="Project Description"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          style={{ width: '100%', padding: '8px', marginBottom: '10px' }}
        />
        <button onClick={() => runAgent('graph')} style={{ padding: '8px 15px', background: '#007bff', color: '#fff', border: 'none', borderRadius: '4px', cursor: 'pointer' }}>
          Run Full Graph
        </button>
      </div>
      <div style={{ display: 'flex', gap: '10px', flexWrap: 'wrap' }}>
        <button onClick={() => runAgent('pm', 'intake')} style={{ padding: '5px 10px' }}>PM Intake</button>
        <button onClick={() => runAgent('ba', 'generate')} style={{ padding: '5px 10px' }}>BA Generate</button>
        <button onClick={() => runAgent('sd', 'generate')} style={{ padding: '5px 10px' }}>SD Generate</button>
        <button onClick={() => runAgent('tl', 'split')} style={{ padding: '5px 10px' }}>TL Split Tasks</button>
        <button onClick={() => runAgent('be')} style={{ padding: '5px 10px' }}>Run BE Dev</button>
        <button onClick={() => runAgent('fe')} style={{ padding: '5px 10px' }}>Run FE Dev</button>
      </div>
    </div>
  )
}

export default AgentControls
