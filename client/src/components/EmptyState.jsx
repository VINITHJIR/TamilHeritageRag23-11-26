import React from 'react';
import { Landmark, Sparkles } from 'lucide-react';

const EXAMPLE_CHIPS = [
  {
    query: 'தஞ்சை பெரிய கோயிலை கட்டியவர் யார்?',
    lang: 'தமிழ்',
    subtitle: 'Thanjavur Big Temple History',
  },
  {
    query: 'Which forts can I visit near Kanyakumari?',
    lang: 'English',
    subtitle: 'Seaside & Coastal Forts',
  },
  {
    query: 'bannari amman kovil la enna festival nadakkum',
    lang: 'Tanglish',
    subtitle: 'Bannari Kundam Festival',
  },
  {
    query: 'ಬನ್ನಾರಿ ಅಮ್ಮನ್ ದೇವಸ್ಥಾನ ಎಲ್ಲಿದೆ?',
    lang: 'ಕನ್ನಡ',
    subtitle: 'Location & How to Reach',
  },
];

export default function EmptyState({ onSelectExample }) {
  return (
    <div className="chat-empty-state">
      <div className="chat-empty-icon" aria-hidden="true">
        <Landmark size={36} />
      </div>

      <h1 className="chat-empty-title">TamilHeritage-GPT</h1>
      <p className="chat-empty-desc">
        Explore 2,000+ years of Tamil Nadu history, architecture, temples, and monuments across Tamil, English, Tanglish, and Kannada.
      </p>

      <div className="chat-example-chips-grid">
        {EXAMPLE_CHIPS.map((chip, idx) => (
          <button
            key={idx}
            type="button"
            className="chat-example-chip"
            onClick={() => onSelectExample(chip.query)}
            aria-label={`Ask example: ${chip.query}`}
          >
            <div className="chat-chip-header">
              <span className="chat-chip-lang">{chip.lang}</span>
              <Sparkles size={12} className="chat-chip-sparkle" />
            </div>
            <div className="chat-chip-query">{chip.query}</div>
            <div className="chat-chip-subtitle">{chip.subtitle}</div>
          </button>
        ))}
      </div>
    </div>
  );
}
