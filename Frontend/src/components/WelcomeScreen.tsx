const SUGGESTIONS = [
  "How many days of annual leave do I get?",
  "What is the parental leave policy?",
  "How do I report a workplace concern confidentially?",
  "What's the notice period after probation?",
  "Can I work remotely from another country?",
  "What does the health insurance cover?",
];

interface Props {
  onPick: (question: string) => void;
}

export function WelcomeScreen({ onPick }: Props) {
  return (
    <div className="welcome">
      <h2>Hi! I'm your HR Assistant.</h2>
      <p>
        Ask me about company policies, leave, benefits, payroll and more. I answer from official HR policy documents and
        show you the sources I used.
      </p>
      <div className="suggestions">
        {SUGGESTIONS.map((q) => (
          <button key={q} type="button" className="suggestion" onClick={() => onPick(q)}>
            {q}
          </button>
        ))}
      </div>
    </div>
  );
}
