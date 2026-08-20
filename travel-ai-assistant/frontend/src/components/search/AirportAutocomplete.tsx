import { useEffect, useState } from "react";

import {
  type AirportSuggestion,
  searchAirports,
} from "../../api/suggestionApi";

type AirportAutocompleteProps = {
  label: string;
  placeholder: string;
  value: AirportSuggestion | null;
  onChange: (value: AirportSuggestion | null) => void;
  error?: string;
};

export function AirportAutocomplete({
  label,
  placeholder,
  value,
  onChange,
  error,
}: AirportAutocompleteProps) {
  const [query, setQuery] = useState(value?.displayName ?? "");
  const [isOpen, setIsOpen] = useState(false);
  const [suggestions, setSuggestions] = useState<AirportSuggestion[]>([]);
  const [highlightedIndex, setHighlightedIndex] = useState(0);
  const [isLoading, setIsLoading] = useState(false);
  const [loadError, setLoadError] = useState("");

  useEffect(() => {
    setQuery(value?.displayName ?? "");
  }, [value]);

  useEffect(() => {
    const trimmed = query.trim();
    if (!isOpen || value?.displayName === query || trimmed.length < 2) {
      setSuggestions([]);
      setIsLoading(false);
      setLoadError("");
      return;
    }

    const controller = new AbortController();
    setIsLoading(true);
    setLoadError("");
    const timeoutId = window.setTimeout(() => {
      searchAirports(trimmed, controller.signal)
        .then((results) => {
          setSuggestions(results);
          setHighlightedIndex(0);
        })
        .catch((requestError) => {
          if ((requestError as Error).name !== "AbortError") {
            setLoadError("Could not load suggestions. Try again.");
            setSuggestions([]);
          }
        })
        .finally(() => setIsLoading(false));
    }, 300);

    return () => {
      window.clearTimeout(timeoutId);
      controller.abort();
    };
  }, [isOpen, query, value?.displayName]);

  function selectAirport(airport: AirportSuggestion) {
    onChange(airport);
    setQuery(airport.displayName);
    setIsOpen(false);
  }

  function handleInput(valueText: string) {
    setQuery(valueText);
    setIsOpen(true);
    if (value && valueText !== value.displayName) {
      onChange(null);
    }
  }

  return (
    <label className="autocomplete-field">
      <span>{label}</span>
      <div className="autocomplete-input-wrap">
        <input
          aria-autocomplete="list"
          aria-expanded={isOpen}
          aria-label={label}
          onKeyDown={(event) => {
            if (!isOpen) {
              return;
            }
            if (event.key === "Escape") {
              setIsOpen(false);
            } else if (event.key === "ArrowDown") {
              event.preventDefault();
              setHighlightedIndex((index) =>
                Math.min(index + 1, Math.max(suggestions.length - 1, 0)),
              );
            } else if (event.key === "ArrowUp") {
              event.preventDefault();
              setHighlightedIndex((index) => Math.max(index - 1, 0));
            } else if (event.key === "Enter" && suggestions[highlightedIndex]) {
              event.preventDefault();
              selectAirport(suggestions[highlightedIndex]);
            }
          }}
          onBlur={() => window.setTimeout(() => setIsOpen(false), 120)}
          onChange={(event) => handleInput(event.target.value)}
          onFocus={() => setIsOpen(true)}
          placeholder={placeholder}
          value={query}
        />
        {value && (
          <button
            aria-label={`Clear ${label}`}
            className="autocomplete-clear"
            onClick={() => {
              onChange(null);
              setQuery("");
              setIsOpen(true);
            }}
            type="button"
          >
            Clear
          </button>
        )}
      </div>
      {isOpen && (
        <div className="autocomplete-menu" role="listbox">
          {query.trim().length < 2 ? (
            <p className="autocomplete-empty">Type at least 2 characters.</p>
          ) : isLoading ? (
            <p className="autocomplete-empty">Searching...</p>
          ) : loadError ? (
            <p className="autocomplete-empty">{loadError}</p>
          ) : suggestions.length === 0 ? (
            <p className="autocomplete-empty">No airport matches found.</p>
          ) : (
            suggestions.map((airport, index) => (
              <button
                className={
                  highlightedIndex === index
                    ? "autocomplete-option autocomplete-option-active"
                    : "autocomplete-option"
                }
                key={airport.code}
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => selectAirport(airport)}
                role="option"
                type="button"
              >
                <strong>{airport.displayName}</strong>
                <small>
                  {airport.provider} · {airport.source === "provider_api" ? "Provider API" : "Provider cache"}
                </small>
              </button>
            ))
          )}
        </div>
      )}
      {value && (
        <small className="field-helper">
          Provider search will use airport code {value.code}.
        </small>
      )}
      {error && <small className="field-error">{error}</small>}
    </label>
  );
}
