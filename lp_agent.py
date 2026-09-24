"""
Interfaccia di ragionamento AI per l'agente LP su Uniswap V3.

Interroga OpenRouter LLM per la classificazione del regime di mercato
e la modulazione dell'ampiezza del range di liquidita', con fallback
immediato e deterministico alle regole matematiche di `lp_manager`.
"""

import json
import logging
import os
import re
from typing import Any, Dict, Optional

import config
from lp_manager import LpManager

logger = logging.getLogger(__name__)


class LpAgent:
    def __init__(self, manager: LpManager):
        self.manager = manager
        self.api_key = config.OPENROUTER_API_KEY
        self.model = config.OPENROUTER_MODEL
        self.system_prompt = self._load_prompt()

    def _load_prompt(self) -> str:
        prompt_path = os.path.join(os.path.dirname(__file__), "system_prompt.txt")
        try:
            with open(prompt_path, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return "Sei un gestore di liquidità concentrata su Uniswap V3. Rispondi solo in JSON."

    def decide(self, status: Dict[str, Any]) -> Dict[str, Any]:
        """
        Produce la decisione operativa.
        Se le chiavi LLM non sono configurate o l'API fallisce, usa la logica deterministica.
        """
        deterministic_plan = self.manager.plan_action(status)

        if not self.api_key:
            return deterministic_plan

        try:
            llm_decision = self._query_llm(status)
            if llm_decision and "action" in llm_decision:
                # Mappa e armonizza
                action_name = str(llm_decision["action"]).lower()
                if action_name in ("hold", "mint", "recenter", "collect_fees"):
                    res = dict(deterministic_plan)
                    res["operation"] = action_name
                    if "range_width_pct" in llm_decision:
                        try:
                            rw = float(llm_decision["range_width_pct"])
                            if 2.0 <= rw <= 30.0:
                                res["range_width_pct"] = rw
                                res["new_range_width_pct"] = rw
                        except (ValueError, TypeError):
                            pass
                    res["reason"] = f"[AI] {llm_decision.get('reason', res.get('reason', ''))}"
                    res["llm_regime"] = llm_decision.get("regime", "neutral")
                    return res
        except Exception as exc:
            logger.warning("Query OpenRouter fallita: %s. Uso logica deterministica.", exc)

        return deterministic_plan

    def _query_llm(self, status: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        from openai import OpenAI

        client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=self.api_key,
        )

        user_content = (
            f"Stato corrente del pool e della posizione LP:\n"
            f"Prezzo corrente: ${status.get('current_price', 0.0):.2f}\n"
            f"Tick: {status.get('current_tick')}\n"
            f"Stato posizione:\n{json.dumps(status.get('position', {}), indent=2)}\n"
            f"Needs Mint: {status.get('needs_mint')}\n"
            f"Needs Recenter: {status.get('needs_recenter')}\n"
            f"Needs Collect: {status.get('needs_collect')}\n"
            f"Determina l'azione ottimale tra hold, mint, recenter, collect_fees."
        )

        completion = client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.2,
            max_tokens=300,
        )

        raw_text = completion.choices[0].message.content or ""
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        return json.loads(raw_text.strip())
