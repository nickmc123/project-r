"""
PROJECT-R LLM LAYER
===================
Interprets natural language questions and explains results.
AI does NOT calculate - only interprets and explains.
All math comes from the deterministic forecast engine.
"""
import re
from typing import Dict, Any, Optional, List, Tuple
from datetime import datetime, timedelta
import json

# Question patterns with intent classification
QUESTION_PATTERNS = [
    # What-if scenarios (check first - most specific)
    (r'what.if.*(add|another|extra|more|increase).*([\d,]+k?).*(revenue|income|sales|week|month)', 'whatif_add_revenue'),
    (r'what.if.*(add|another|extra|more|increase).*([\d,]+k?)', 'whatif_add_revenue'),
    (r'what.if.*(reduce|cut|decrease|less|lower).*(expense|cost|spend).*([\d,]+k?)', 'whatif_reduce_expense'),
    (r'what.if.*(reduce|cut|decrease|less|lower).*([\d,]+k?)', 'whatif_reduce_expense'),
    (r'what.if.*([\d,]+k?).*(more|extra|additional).*(revenue|income|sales)', 'whatif_add_revenue'),
    (r'what.if.*([\d,]+k?).*(less|lower|reduce).*(expense|cost)', 'whatif_reduce_expense'),
    (r'(add|increase).*([\d,]+k?).*(week|month|revenue|income)', 'whatif_add_revenue'),
    (r'(reduce|cut|decrease).*(expense|cost).*([\d,]+k?)', 'whatif_reduce_expense'),
    
    # Balance questions
    (r'(what|how much).*(balance|have|got|money)', 'current_balance'),
    (r'balance.*(today|now|current)', 'current_balance'),
    (r'how.*doing', 'current_balance'),
    
    # Low point questions
    (r'(low|lowest|minimum|min).*(point|balance|when|hit)', 'low_point'),
    (r'when.*(tight|trouble|low|short)', 'low_point'),
    (r'(worst|danger|risk)', 'low_point'),
    
    # High point questions
    (r'(high|highest|maximum|max|peak).*(point|balance|when)', 'high_point'),
    (r'when.*(best|most|peak)', 'high_point'),
    
    # Forecast questions
    (r'(forecast|project|predict|expect).*(day|week|month|\d+)', 'forecast'),
    (r'(next|coming).*(day|week|month)', 'forecast'),
    (r'what.*look.*like', 'forecast'),
    (r'show.*(\d+).*(day|week)', 'forecast'),
    
    # Profit questions
    (r'(profit|margin|making|earning|net)', 'profit'),
    (r'how much.*(make|earn|profit)', 'profit'),
    
    # Expense questions
    (r'(expense|spend|cost|outflow|debit)', 'expenses'),
    (r'where.*(money|cash).*going', 'expenses'),
    (r'what.*paying', 'expenses'),
    
    # Revenue questions  
    (r'(revenue|income|inflow|credit|earning)', 'revenue'),
    (r'where.*(money|cash).*coming', 'revenue'),
    (r'what.*bringing', 'revenue'),
    
    # Category questions
    (r'(category|categories|group|breakdown)', 'categories'),
    (r'break.*down', 'categories'),
    
    # Trend questions
    (r'(trend|trending|direction|going up|going down)', 'trends'),
    (r'(improve|worse|better|decline)', 'trends'),
    
    # Timing questions
    (r'when.*(pay|afford|safe|can i)', 'timing'),
    (r'(can|should).*(pay|spend|afford)', 'timing'),
    
    # Comparison questions
    (r'(compare|versus|vs|difference|than)', 'comparison'),
    (r'(last|previous).*(month|week|period)', 'comparison'),
]

# Response templates for each intent
RESPONSE_TEMPLATES = {
    'current_balance': [
        "Your current balance is **${balance:,.0f}**.",
        "You have **${balance:,.0f}** in the bank right now.",
        "Current cash position: **${balance:,.0f}**",
    ],
    'low_point': [
        "Your projected low point is **${amount:,.0f}** on **{date}**.",
        "Heads up! You'll hit **${amount:,.0f}** around **{date}** - that's your tightest spot.",
        "Watch out for **{date}** - balance drops to **${amount:,.0f}**.",
    ],
    'high_point': [
        "Your high point will be **${amount:,.0f}** on **{date}**.",
        "Peak balance of **${amount:,.0f}** expected on **{date}**.",
        "Best position: **${amount:,.0f}** around **{date}**.",
    ],
    'profit': [
        "Your 30-day rolling profit is **${amount:,.0f}**.",
        "You're averaging **${amount:,.0f}** profit over the past 30 days.",
        "Monthly profit trend: **${amount:,.0f}**",
    ],
    'healthy': [
        "Looking healthy! 💚 Plenty of runway ahead.",
        "Cash flow looks solid! 💪",
        "You're in good shape - comfortable margins throughout.",
    ],
    'tight': [
        "Things get tight around {date}. Keep an eye on it. ⚠️",
        "Watch the period around {date} - margins are thin.",
        "Heads up: {date} looks tight. You might want a buffer.",
    ],
    'ok': [
        "Cash flow is manageable but not comfortable.",
        "You'll get through, but it's not a lot of cushion.",
        "Workable, but I'd watch it closely.",
    ],
}


class QuestionInterpreter:
    """Interprets natural language questions about cash flow."""
    
    def __init__(self):
        self.patterns = [(re.compile(p, re.IGNORECASE), intent) 
                        for p, intent in QUESTION_PATTERNS]
    
    def classify(self, question: str) -> Tuple[str, Dict[str, Any]]:
        """
        Classify a question and extract parameters.
        Returns (intent, params)
        """
        question_lower = question.strip().lower()
        
        # Extract numeric parameters
        params = {}
        
        # Look for day counts
        day_match = re.search(r'(\d+)\s*day', question_lower)
        if day_match:
            params['days'] = int(day_match.group(1))
        
        # Look for week counts
        week_match = re.search(r'(\d+)\s*week', question_lower)
        if week_match:
            params['weeks'] = int(week_match.group(1))
            params['days'] = params.get('days', 0) + int(week_match.group(1)) * 7
        
        # Look for month counts
        month_match = re.search(r'(\d+)\s*month', question_lower)
        if month_match:
            params['months'] = int(month_match.group(1))
            params['days'] = params.get('days', 0) + int(month_match.group(1)) * 30
        
        # Look for dollar amounts with k/K suffix (e.g., 5k, $10K)
        amount_matches = re.findall(r'\$?([\d,]+)\s*k', question_lower, re.IGNORECASE)
        if amount_matches:
            # Convert "5k" to 5000
            params['amount'] = float(amount_matches[0].replace(',', '')) * 1000
        else:
            # Look for regular dollar amounts
            amount_match = re.search(r'\$?([\d,]+)(?!k)', question_lower)
            if amount_match:
                val = float(amount_match.group(1).replace(',', ''))
                # If it's a small number, likely meant as thousands
                if val < 100:
                    params['amount'] = val * 1000
                else:
                    params['amount'] = val
        
        # Determine frequency for what-if scenarios
        if 'week' in question_lower:
            params['frequency'] = 'weekly'
            params['multiplier'] = 4.33  # weeks per month
        elif 'month' in question_lower:
            params['frequency'] = 'monthly'
            params['multiplier'] = 1
        elif 'day' in question_lower:
            params['frequency'] = 'daily'
            params['multiplier'] = 30  # days per month
        else:
            # Default to weekly for revenue additions
            params['frequency'] = 'weekly'
            params['multiplier'] = 4.33
        
        # Match against patterns
        for pattern, intent in self.patterns:
            if pattern.search(question_lower):
                return intent, params
        
        # Default to general forecast if no match
        return 'general', params
    
    def extract_timeframe(self, question: str) -> int:
        """Extract the number of days to forecast."""
        question = question.lower()
        
        # Check for explicit day counts
        day_match = re.search(r'(\d+)\s*day', question)
        if day_match:
            return min(int(day_match.group(1)), 365)
        
        # Check for week counts
        week_match = re.search(r'(\d+)\s*week', question)
        if week_match:
            return min(int(week_match.group(1)) * 7, 365)
        
        # Check for month counts
        month_match = re.search(r'(\d+)\s*month', question)
        if month_match:
            return min(int(month_match.group(1)) * 30, 365)
        
        # Default timeframes based on keywords
        if 'month' in question:
            return 30
        if 'week' in question:
            return 7
        
        # Default to 30 days
        return 30


class ResponseGenerator:
    """Generates natural language responses from forecast data."""
    
    def __init__(self):
        self.interpreter = QuestionInterpreter()
    
    def format_date(self, date_str: str) -> str:
        """Format a date string for display."""
        try:
            dt = datetime.strptime(date_str, '%Y-%m-%d')
            return dt.strftime('%B %d')
        except:
            return date_str
    
    def format_currency(self, amount: float) -> str:
        """Format a currency amount."""
        if amount >= 0:
            return f"${amount:,.0f}"
        return f"-${abs(amount):,.0f}"
    
    def get_status_description(self, status: str) -> str:
        """Get a description for the status."""
        descriptions = {
            'HEALTHY': "Your cash position is healthy with comfortable margins.",
            'OK': "Cash flow is manageable but worth monitoring.",
            'TIGHT': "Cash gets tight at some points - keep a close eye on it.",
            'NO DATA': "No forecast data available yet.",
        }
        return descriptions.get(status, "Status unknown.")
    
    def generate_response(self, question: str, forecast_data: Dict[str, Any]) -> str:
        """
        Generate a natural language response to a question.
        
        Args:
            question: The user's question
            forecast_data: Data from the forecast engine
        
        Returns:
            Natural language response
        """
        intent, params = self.interpreter.classify(question)
        
        # Extract common data
        current_balance = forecast_data.get('current_balance', 0)
        low_point = forecast_data.get('low_point', {})
        high_point = forecast_data.get('high_point', {})
        profit = forecast_data.get('profit_30d', 0)
        status = forecast_data.get('status', 'NO DATA')
        
        # Generate response based on intent
        if intent == 'current_balance':
            response = f"Your current balance is **{self.format_currency(current_balance)}**."
            response += f"\n\n{self.get_status_description(status)}"
            return response
        
        elif intent == 'low_point':
            if low_point:
                amount = low_point.get('amount', 0)
                date = self.format_date(low_point.get('date', ''))
                response = f"Your projected low point is **{self.format_currency(amount)}** on **{date}**."
                
                if amount < 10000:
                    response += "\n\n⚠️ That's pretty tight - you might want to plan ahead."
                elif amount < 50000:
                    response += "\n\nNot a lot of cushion there - worth keeping an eye on."
                else:
                    response += "\n\n💚 Good buffer even at the low point."
            else:
                response = "I don't have enough data to project your low point yet."
            return response
        
        elif intent == 'high_point':
            if high_point:
                amount = high_point.get('amount', 0)
                date = self.format_date(high_point.get('date', ''))
                response = f"Your peak balance will be **{self.format_currency(amount)}** around **{date}**."
            else:
                response = "I don't have enough data to project your high point yet."
            return response
        
        elif intent == 'profit':
            if profit:
                response = f"Your 30-day rolling profit is **{self.format_currency(profit)}**."
                if profit > 0:
                    response += "\n\n📈 You're trending positive - nice work!"
                elif profit < 0:
                    response += "\n\n📉 You're running at a loss right now."
                else:
                    response += "\n\nBreaking even."
            else:
                response = "I need more transaction history to calculate your profit trend."
            return response
        
        elif intent == 'forecast':
            days = params.get('days', 30)
            response = self._generate_forecast_response(forecast_data, days)
            return response
        
        elif intent == 'expenses':
            categories = forecast_data.get('categories', {})
            expense_cats = [c for c in categories.values() if c.get('type') == 'debit']
            if expense_cats:
                response = "**Your expense categories:**\n"
                for cat in sorted(expense_cats, key=lambda x: x.get('total', 0)):
                    response += f"- {cat['name']}: {self.format_currency(abs(cat.get('total', 0)))}/month\n"
            else:
                response = "No expense categories set up yet. Import some transactions to get started!"
            return response
        
        elif intent == 'revenue':
            categories = forecast_data.get('categories', {})
            revenue_cats = [c for c in categories.values() if c.get('type') == 'credit']
            if revenue_cats:
                response = "**Your revenue categories:**\n"
                for cat in sorted(revenue_cats, key=lambda x: x.get('total', 0), reverse=True):
                    response += f"- {cat['name']}: {self.format_currency(cat.get('total', 0))}/month\n"
            else:
                response = "No revenue categories set up yet. Import some transactions to get started!"
            return response
        
        elif intent == 'categories':
            categories = forecast_data.get('categories', {})
            if categories:
                response = "**Your transaction categories:**\n\n"
                
                credits = [c for c in categories.values() if c.get('type') == 'credit']
                debits = [c for c in categories.values() if c.get('type') == 'debit']
                
                if credits:
                    response += "**Income:**\n"
                    for cat in credits:
                        response += f"- {cat['name']}: {self.format_currency(cat.get('net_total', 0))}\n"
                
                if debits:
                    response += "\n**Expenses:**\n"
                    for cat in debits:
                        response += f"- {cat['name']}: {self.format_currency(abs(cat.get('net_total', 0)))}\n"
            else:
                response = "No categories set up yet. Import transactions and I'll help you categorize them!"
            return response
        
        elif intent == 'trends':
            response = self._generate_trend_response(forecast_data)
            return response
        
        elif intent == 'timing':
            amount = params.get('amount', 0)
            if amount > 0:
                response = self._generate_timing_response(forecast_data, amount)
            else:
                response = "What amount are you thinking about? Tell me a number and I'll let you know the best time."
            return response
        
        elif intent == 'comparison':
            response = "Historical comparison coming soon! For now, I can tell you about your current forecast."
            return response
        
        elif intent == 'whatif_add_revenue':
            response = self._generate_whatif_revenue_response(forecast_data, params)
            return response
        
        elif intent == 'whatif_reduce_expense':
            response = self._generate_whatif_expense_response(forecast_data, params)
            return response
        
        else:
            # General response
            response = self._generate_summary_response(forecast_data)
            return response
    
    def _generate_forecast_response(self, data: Dict, days: int) -> str:
        """Generate a forecast overview response."""
        current = data.get('current_balance', 0)
        low = data.get('low_point', {})
        high = data.get('high_point', {})
        status = data.get('status', 'NO DATA')
        
        response = f"**{days}-Day Forecast**\n\n"
        response += f"Starting balance: {self.format_currency(current)}\n"
        
        if low:
            response += f"Low point: {self.format_currency(low.get('amount', 0))} on {self.format_date(low.get('date', ''))}\n"
        
        if high:
            response += f"High point: {self.format_currency(high.get('amount', 0))} on {self.format_date(high.get('date', ''))}\n"
        
        response += f"\n**Status: {status}**"
        
        return response
    
    def _generate_trend_response(self, data: Dict) -> str:
        """Generate a trend analysis response."""
        profit = data.get('profit_30d', 0)
        
        if profit > 10000:
            return "📈 **Trending Up** - You're gaining ground, averaging {}/month profit.".format(
                self.format_currency(profit))
        elif profit > 0:
            return "📊 **Slightly Positive** - Small gains, averaging {}/month.".format(
                self.format_currency(profit))
        elif profit > -10000:
            return "📉 **Slightly Negative** - Minor losses, averaging {}/month.".format(
                self.format_currency(profit))
        else:
            return "🔻 **Trending Down** - Losing {}/month - worth investigating.".format(
                self.format_currency(abs(profit)))
    
    def _generate_timing_response(self, data: Dict, amount: float) -> str:
        """Generate advice about timing a payment."""
        current = data.get('current_balance', 0)
        low = data.get('low_point', {})
        
        if current < amount:
            return f"You don't have enough for a {self.format_currency(amount)} payment right now. Current balance is {self.format_currency(current)}."
        
        low_amount = low.get('amount', 0) if low else current
        low_date = low.get('date', '') if low else ''
        
        buffer_after = low_amount - amount
        
        if buffer_after > 20000:
            return f"✅ You can safely pay {self.format_currency(amount)} anytime. Even at your low point ({self.format_date(low_date)}), you'd still have {self.format_currency(buffer_after)}."
        elif buffer_after > 0:
            return f"⚠️ You could pay {self.format_currency(amount)}, but it would leave you tight around {self.format_date(low_date)} with only {self.format_currency(buffer_after)} buffer."
        else:
            return f"❌ Paying {self.format_currency(amount)} would put you negative around {self.format_date(low_date)}. I'd wait or find additional revenue first."
    
    def _generate_whatif_revenue_response(self, data: Dict, params: Dict) -> str:
        """Generate response for what-if revenue addition scenarios."""
        amount = params.get('amount', 0)
        frequency = params.get('frequency', 'weekly')
        multiplier = params.get('multiplier', 4.33)
        
        if amount == 0:
            return "I'd love to run that scenario! How much additional revenue are you thinking? (e.g., '$5K per week')"
        
        # Calculate monthly impact
        monthly_impact = amount * multiplier
        
        # Get current data
        current_balance = data.get('current_balance', 0)
        low_point = data.get('low_point', {})
        current_profit = data.get('profit_30d', 0)
        
        low_amount = low_point.get('amount', current_balance) if low_point else current_balance
        low_date = low_point.get('date', '') if low_point else ''
        
        # Calculate adjusted values
        adjusted_low = low_amount + (monthly_impact / 2)  # Assume half-month average impact
        adjusted_profit = current_profit + monthly_impact
        
        response = f"**📈 What-If Scenario: +{self.format_currency(amount)}/{frequency}**\n\n"
        response += f"That would add **{self.format_currency(monthly_impact)}/month** to your cash flow!\n\n"
        response += f"**Current → Adjusted:**\n"
        response += f"- 30-Day Cash Change: {self.format_currency(current_profit)} → **{self.format_currency(adjusted_profit)}**\n"
        
        if low_point:
            response += f"- Low Point ({self.format_date(low_date)}): {self.format_currency(low_amount)} → **{self.format_currency(adjusted_low)}**\n"
        
        # Add assessment
        if adjusted_low > 50000:
            response += "\n✅ **That would give you a comfortable cushion!**"
        elif adjusted_low > 20000:
            response += "\n👍 **Much better - that helps your margins significantly.**"
        elif adjusted_low > 0:
            response += "\n⚠️ **Better, but still tight at the low point.**"
        else:
            response += "\n❌ **Even with this increase, you'd still hit negative. Need more!**"
        
        return response
    
    def _generate_whatif_expense_response(self, data: Dict, params: Dict) -> str:
        """Generate response for what-if expense reduction scenarios."""
        amount = params.get('amount', 0)
        frequency = params.get('frequency', 'monthly')
        multiplier = params.get('multiplier', 1)
        
        if amount == 0:
            return "I'd love to run that scenario! How much expense reduction are you thinking? (e.g., 'reduce expenses by $10K')"
        
        # Calculate monthly impact (expense reductions are positive to cash flow)
        monthly_impact = amount * multiplier
        
        # Get current data
        current_balance = data.get('current_balance', 0)
        low_point = data.get('low_point', {})
        current_profit = data.get('profit_30d', 0)
        
        low_amount = low_point.get('amount', current_balance) if low_point else current_balance
        low_date = low_point.get('date', '') if low_point else ''
        
        # Calculate adjusted values
        adjusted_low = low_amount + (monthly_impact / 2)  # Assume half-month average impact
        adjusted_profit = current_profit + monthly_impact
        
        response = f"**📉 What-If Scenario: Cut {self.format_currency(amount)}/month in expenses**\n\n"
        response += f"That would save **{self.format_currency(monthly_impact)}/month**!\n\n"
        response += f"**Current → Adjusted:**\n"
        response += f"- 30-Day Cash Change: {self.format_currency(current_profit)} → **{self.format_currency(adjusted_profit)}**\n"
        
        if low_point:
            response += f"- Low Point ({self.format_date(low_date)}): {self.format_currency(low_amount)} → **{self.format_currency(adjusted_low)}**\n"
        
        # Add assessment
        if adjusted_low > 50000:
            response += "\n✅ **That puts you in great shape!**"
        elif adjusted_low > 20000:
            response += "\n👍 **Nice improvement - much more breathing room.**"
        elif adjusted_low > 0:
            response += "\n⚠️ **Helps, but you're still pretty tight at the low point.**"
        else:
            response += "\n❌ **Even with this cut, projections still go negative. Need more adjustments.**"
        
        return response
    
    def _generate_summary_response(self, data: Dict) -> str:
        """Generate a general summary response."""
        current = data.get('current_balance', 0)
        low = data.get('low_point', {})
        high = data.get('high_point', {})
        profit = data.get('profit_30d', 0)
        status = data.get('status', 'NO DATA')
        
        response = f"**Cash Flow Summary**\n\n"
        response += f"Current Balance: {self.format_currency(current)}\n"
        
        if low:
            response += f"Low Point: {self.format_currency(low.get('amount', 0))} ({self.format_date(low.get('date', ''))})\n"
        
        if high:
            response += f"High Point: {self.format_currency(high.get('amount', 0))} ({self.format_date(high.get('date', ''))})\n"
        
        if profit:
            response += f"30-Day Profit: {self.format_currency(profit)}\n"
        
        response += f"\n**Status: {status}**\n"
        response += self.get_status_description(status)
        
        return response


class LLMService:
    """
    Main LLM service that handles question answering.
    Uses pattern matching for common questions, with optional
    Ollama integration for complex queries.
    """
    
    def __init__(self, ollama_url: Optional[str] = None):
        self.response_generator = ResponseGenerator()
        self.ollama_url = ollama_url
        self.ollama_available = False
        
        if ollama_url:
            self._check_ollama()
    
    def _check_ollama(self):
        """Check if Ollama is available."""
        try:
            import httpx
            response = httpx.get(f"{self.ollama_url}/api/tags", timeout=5)
            self.ollama_available = response.status_code == 200
        except:
            self.ollama_available = False
    
    async def answer(self, question: str, forecast_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Answer a question about cash flow.
        
        Args:
            question: Natural language question
            forecast_data: Data from forecast engine
        
        Returns:
            Response dict with answer and metadata
        """
        # Classify the question
        interpreter = QuestionInterpreter()
        intent, params = interpreter.classify(question)
        
        # Generate response using patterns
        response = self.response_generator.generate_response(question, forecast_data)
        
        return {
            'question': question,
            'intent': intent,
            'params': params,
            'answer': response,
            'source': 'pattern',  # or 'ollama' if we used the LLM
            'timestamp': datetime.now().isoformat(),
        }
    
    async def answer_with_ollama(self, question: str, forecast_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        Use Ollama for complex questions that patterns can't handle.
        Falls back to patterns if Ollama is unavailable.
        """
        if not self.ollama_available:
            return await self.answer(question, forecast_data)
        
        try:
            import httpx
            
            # Build context for the LLM
            context = self._build_context(forecast_data)
            
            prompt = f"""You are a helpful cash flow assistant. Answer the user's question based ONLY on the data provided.
Do NOT make up numbers or do calculations - just explain what the data shows.

CASH FLOW DATA:
{context}

USER QUESTION: {question}

Provide a clear, friendly response. Use markdown formatting. Keep it concise."""

            async with httpx.AsyncClient() as client:
                response = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json={
                        "model": "tinyllama",
                        "prompt": prompt,
                        "stream": False,
                    },
                    timeout=30,
                )
                
                if response.status_code == 200:
                    result = response.json()
                    return {
                        'question': question,
                        'intent': 'ollama',
                        'params': {},
                        'answer': result.get('response', ''),
                        'source': 'ollama',
                        'timestamp': datetime.now().isoformat(),
                    }
        except Exception as e:
            print(f"Ollama error: {e}")
        
        # Fallback to patterns
        return await self.answer(question, forecast_data)
    
    def _build_context(self, data: Dict[str, Any]) -> str:
        """Build context string for LLM."""
        lines = []
        
        if 'current_balance' in data:
            lines.append(f"Current Balance: ${data['current_balance']:,.0f}")
        
        if 'low_point' in data and data['low_point']:
            lp = data['low_point']
            lines.append(f"Low Point: ${lp.get('amount', 0):,.0f} on {lp.get('date', 'unknown')}")
        
        if 'high_point' in data and data['high_point']:
            hp = data['high_point']
            lines.append(f"High Point: ${hp.get('amount', 0):,.0f} on {hp.get('date', 'unknown')}")
        
        if 'profit_30d' in data:
            lines.append(f"30-Day Profit: ${data['profit_30d']:,.0f}")
        
        if 'status' in data:
            lines.append(f"Status: {data['status']}")
        
        return "\n".join(lines)


# Singleton instance
_llm_service = None

def get_llm_service(ollama_url: Optional[str] = None) -> LLMService:
    """Get or create the LLM service singleton."""
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService(ollama_url)
    return _llm_service
