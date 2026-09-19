import React, { useState, useEffect } from 'react';
import { checkWaiting, provideInput } from '../../api';
import { useStream } from '../stream';
import { useI18n } from '../../i18n';

function InteractionForm({ workspace }) {
  const { t } = useI18n();
  const [waiting, setWaiting] = useState({ questions: [] });
  const [answers, setAnswers] = useState({});
  const { on } = useStream();

  useEffect(() => {
    if (!workspace) return;

    const fetchWaiting = async () => {
      try {
        const res = await checkWaiting(workspace);
        setWaiting(res.data);
      } catch {
        // console.error("Failed to fetch waiting status", e);
      }
    };

    fetchWaiting();
    return on('app', (ev) => { if (ev.type === 'flow_runs.changed') fetchWaiting(); });
  }, [workspace, on]);

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      await provideInput({ answers, workspace });
      setAnswers({});
      setWaiting({ questions: [] });
      alert(t('flowInteractionForm.answersSent'));
    } catch {
      alert(t('flowInteractionForm.sendFailed'));
    }
  };

  if (!waiting.questions || waiting.questions.length === 0) return null;

  return (
    <div className="bg-yellow-50 border border-yellow-200 rounded-lg p-6 space-y-4">
      <h3 className="text-lg font-medium text-yellow-800">{t('flowInteractionForm.userInputRequired')}</h3>
      <form onSubmit={handleSubmit} className="space-y-4">
        {waiting.questions.map((q, i) => (
          <div key={i} className="space-y-1">
            <label className="block text-sm font-medium text-yellow-700">{q}</label>
            <input
              type="text"
              className="w-full border border-yellow-300 rounded-md px-3 py-2 focus:ring-yellow-500 focus:border-yellow-500"
              onChange={(e) => setAnswers({ ...answers, [q]: e.target.value })}
              required
            />
          </div>
        ))}
        <button
          type="submit"
          className="bg-yellow-600 text-white px-4 py-2 rounded-md hover:bg-yellow-700 transition-colors font-medium shadow-sm"
        >
          {t('flowInteractionForm.submitAnswers')}
        </button>
      </form>
    </div>
  );
}

export default InteractionForm;
