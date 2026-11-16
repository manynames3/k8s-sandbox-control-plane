/** Snippets behind the "Try an attack" buttons. The data lives in attacks.json so that the recorder
 *  (scripts/record_showcase.py) runs exactly the same code the buttons load. They mirror
 *  tests/attack/test_attacks.py; each `expect` says what a healthy cluster does. */
import data from "./attacks.json";

export type Language = "python3.12" | "node20";

export interface Attack {
  id: string;
  label: string;
  language: Language;
  timeout: number;
  expect: string;
  code: string;
}

export const ATTACKS = data.attacks as Attack[];
export const HELLO = data.hello as Record<Language, string>;
