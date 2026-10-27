import { Search } from 'lucide-react';

interface SearchBarProps {
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  width?: number;
}

export default function SearchBar({ value, onChange, placeholder = '검색', width }: SearchBarProps) {
  return (
    <div className="searchbar" style={width ? { width } : undefined}>
      <Search size={16} />
      <input
        type="text"
        value={value}
        placeholder={placeholder}
        onChange={(e) => onChange(e.target.value)}
      />
    </div>
  );
}
