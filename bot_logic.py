            return True, "Potential EVM Contract Address"
            
        return False, None
        
    def greet_new_members(self, message):
        try:
            logger.info("New members detected, waiting 5 minutes (300 seconds) before greeting...")
            
            # --- Change: 5 minutes delay ---
            time.sleep(300) 
            
            now_utc = self._get_current_utc_time().strftime('%Y-%m-%d %H:%M:%S')
            
            for member in message.new_chat_members:
                # Save member to DB immediately
                self._update_member_info(member.id, member.username, now_utc, last_interacted_date=now_utc)
                
                first_name = (member.first_name or "fren").replace('_', '\\_').replace('*', '\\*').replace('[', '\\[').replace('`', '\\`')
                
                # Use new response category: GREET_NEW_MEMBERS_DELAYED
                welcome_text = random.choice(self.responses.get("GREET_NEW_MEMBERS_DELAYED", [])).format(name=f"[{first_name}](tg://user?id={member.id})")
                
                try:
                    self.bot.send_message(message.chat.id, welcome_text, parse_mode="Markdown")
                    logger.info(f"Delayed greeting sent to new member: {member.id}")
                except Exception as e:
                    logger.error(f"Failed to send welcome message: {e}")
        except Exception as e:
            logger.error(f"Error in greet_new_members: {e}", exc_info=True)

    def send_welcome(self, message):
        welcome_text = (" 🐸  *Welcome to the official NextPepe ($NPEPE) Bot!* 🔥 \n\n"
                        "I am the spirit of the NPEPEVERSE, here to guide you. Use the buttons below or ask me anything!")
        try:
            self.bot.reply_to(message, welcome_text, reply_markup=self.main_menu_keyboard(), parse_mode="Markdown")
        except Exception as e:
            logger.error(f"Failed to send /start: {e}")
            
    def handle_callback_query(self, call): 
        try:
            if call.data == "hype":
                hype_text = "LFG! HODL tight, fren!" 
                self.bot.answer_callback_query(call.id, text=hype_text, show_alert=True)
            elif call.data == "about":
                self.bot.answer_callback_query(call.id)
                about_text = (" 🚀  *$NPEPE* is the next evolution of meme power!\n"
                              "We are a community-driven force born on *Pump.fun*.\n\n"
                              "This is 100% pure, unadulterated meme energy. Welcome to the NPEPEVERSE!  🐸 ")
                self.bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=about_text, reply_markup=self.main_menu_keyboard(), parse_mode="Markdown")
            elif call.data == "ca":
                self.bot.answer_callback_query(call.id)
                ca_text = f" 🔗  *Contract Address:*\n`{Config.CONTRACT_ADDRESS()}`"
                self.bot.edit_message_text(chat_id=call.message.chat.id, message_id=call.message.message_id, text=ca_text, reply_markup=self.main_menu_keyboard(), parse_mode="Markdown")
            else:
                self.bot.answer_callback_query(call.id, text="Action not recognized.")
        except Exception as e:
            logger.error(f"Error in callback handler: {e}", exc_info=True)
            try:
                self.bot.answer_callback_query(call.id, text="Sorry, something went wrong!", show_alert=True)
            except:
                pass
            
    def _is_a_question(self, text): 
        if not text or not isinstance(text, str):
            return False
        txt = text.strip().lower()
        if txt.endswith('?'):
            return True
        question_words = ['what', 'how', 'when', 'where', 'why', 'who', 'can', 'could', 'is', 'are', 'do', 'does', 'explain']
        return any(txt.startswith(w) for w in question_words)

    def handle_all_text(self, message):
        try:
            if not message: return
            
            if message.chat.type in ['group', 'supergroup']:
                chat_id = message.chat.id
                user_id = message.from_user.id
                self._update_admin_ids(chat_id)
                
                is_exempt = user_id in self.admin_ids
                if Config.GROUP_OWNER_ID() and str(user_id) == str(Config.GROUP_OWNER_ID()):
                    is_exempt = True
                if not is_exempt:
                    is_spam, reason = self._is_spam_or_ad(message)
                    if is_spam:
                        try:
                            self.bot.delete_message(chat_id, message.message_id)
                            logger.info(f"Deleted message {message.message_id} from {user_id} reason: {reason}")
                        except Exception as e:
                            logger.error(f"Failed to delete spam message: {e}")
                        return
      
            text = (message.text or message.caption or "")
            if not text: return
            
            lower_text = text.lower().strip()
            chat_id = message.chat.id
            
            # Owner Tag Check
            if (Config.GROUP_OWNER_ID() and message.entities and message.chat.type in ['group', 'supergroup']):
                for entity in message.entities:
                    if getattr(entity, 'type', None) == 'text_mention' and getattr(entity, 'user', None):
                        if str(entity.user.id) == str(Config.GROUP_OWNER_ID()):
                            # Respond with BOT_IDENTITY for "who is owner" questions
                            self.bot.send_message(chat_id, random.choice(self.responses.get("BOT_IDENTITY", []))) 
                            return
            
            # CA & Buy Check
            if any(kw in lower_text for kw in ["ca", "contract", "address"]):
                self.bot.send_message(chat_id, f"Here is the contract address, fren:\n\n`{Config.CONTRACT_ADDRESS()}`", parse_mode="Markdown")
                return
            if any(kw in lower_text for kw in ["how to buy", "where to buy", "buy npepe"]):
                self.bot.send_message(chat_id, " 💰  You can buy *$NPEPE* on Pump.fun! The portal to the moon is one click away!  🚀 ", parse_mode="Markdown", reply_markup=self.main_menu_keyboard())
                return
            
            # --- NEW BOT LOGIC ---
            
            # Bot Identity Response
            if any(kw in lower_text for kw in ["what are you", "what is this bot", "are you a bot", "what kind of bot", "who made you"]):
                logger.info("Bot identity question detected, responding immediately...")
                self.bot.send_message(chat_id, random.choice(self.responses.get("BOT_IDENTITY", [])))
                return
            
            # Birthday Response
            if any(kw in lower_text for kw in ["my birthday", "my bday", "it's my birthday", "my birthday this week"]) and message.chat.type in ['group', 'supergroup']:
                try:
                    # Use BIRTHDAY_GREETING (Tugas 3)
                    self.bot.reply_to(message, random.choice(self.responses.get("BIRTHDAY_GREETING", [])).format(name=message.from_user.first_name), parse_mode="Markdown")
                    return
                except Exception as e:
                    logger.error(f"Failed to send birthday greeting: {e}")
                    
            # Collaboration Response (Retained)
            if any(kw in lower_text for kw in ["collab", "partner", "promote", "help grow", "shill", "marketing"]):
                self.bot.send_message(chat_id, random.choice(self.responses.get("COLLABORATION_RESPONSE", [])))
                return
            
            # AI Response for Questions
            if self.groq_client and self._is_a_question(text):
                thinking_message = None
                try:
                    thinking_message = self.bot.send_message(chat_id, " 🐸  The NPEPE oracle is consulting the memes...")
                    system_prompt = (
                        "You are a crypto community bot for $NPEPE. Funny, enthusiastic, chaotic. "
                        "Use slang: ‘fren’, ‘WAGMI’, ‘HODL’, ‘based’, ‘LFG’, ‘ribbit’. Keep answers short."
                    )
                    chat_completion = self.groq_client.chat.completions.create(
                        messages=[{"role": "system", "content": system_prompt}, {"role": "user", "content": text}],
                        model="llama3-8b-8192", 
                        temperature=0.7, max_tokens=150
                    )
                    ai_response = chat_completion.choices[0].message.content
                    try:
                        self.bot.edit_message_text(ai_response, chat_id=chat_id, message_id=thinking_message.message_id)
                    except Exception:
                        self.bot.send_message(chat_id, ai_response)
                except Exception as e:
                    logger.error(f"AI response error: {e}", exc_info=True)
                    fallback = random.choice(self.responses.get("FINAL_FALLBACK", ["Sorry fren, can’t answer now."]))
                    try:
                        if thinking_message: self.bot.edit_message_text(fallback, chat_id=chat_id, message_id=thinking_message.message_id)
                        else: self.bot.send_message(chat_id, fallback)
                    except Exception as ex:
                        logger.error(f"Failed to send fallback: {ex}")
                return
            
        except Exception as e:
            logger.error(f"FATAL ERROR processing message: {e}", exc_info=True)

    # --- NEW SCHEDULED TASK FUNCTIONS ---

    def send_daily_random_greeting(self):
        """Task 1: Greets 3 random members daily (turn-based mode)."""
        group_id = Config.GROUP_CHAT_ID()
        if not group_id: return
        
        # 1. Get all members from DB
        all_members = self._get_all_active_members()
        if not all_members:
            logger.warning("No members in DB to greet.")
            return

        # 2. Sort by last_interacted_date (for rotation/turn)
        sorted_members = sorted(all_members, key=lambda x: datetime.strptime(x[3], '%Y-%m-%d %H:%M:%S') if x[3] else datetime.min)
        
        members_to_greet = []
        now_ts_str = self._get_current_utc_time().strftime('%Y-%m-%d %H:%M:%S')
        
        for i in range(3): # Try to greet 3 members
            if not sorted_members: break
            
            member_data = sorted_members.pop(0) 
            user_id, username, joined_date, last_interacted_date, last_thanked_month = member_data

            try:
                # 3. Check membership in group (skip if no longer a member)
                chat_member = self.bot.get_chat_member(group_id, user_id)
                if chat_member.status in ['member', 'administrator', 'creator']:
                    members_to_greet.append((user_id, username))
                    
                    # Mark as greeted in DB
                    self._update_member_info(user_id, last_interacted_date=now_ts_str)
                else:
                    logger.info(f"Member {user_id} is no longer active. Skipping.")
            except telebot.apihelper.ApiTelegramException as e:
                if "user not found in chat" in str(e):
                    logger.info(f"Member {user_id} left the group. Skipping.")
                else:
                    logger.error(f"Error checking membership for {user_id}: {e}")
            except Exception as e:
                logger.error(f"Error checking membership for {user_id}: {e}")

        # 4. Send greetings to 3 valid members
        if members_to_greet:
            message_parts = []
            for user_id, username in members_to_greet:
                # Use DAILY_GREETING (Task 1)
                greeting = random.choice(self.responses.get("DAILY_GREETING", [])).format(mention=f"[{username or 'Fren'}](tg://user?id={user_id})")
                message_parts.append(greeting)
            
            final_message = "\n\n---\n\n".join(message_parts)
            try:
                self.bot.send_message(group_id, final_message, parse_mode="Markdown")
                logger.info(f"Sent random daily greeting to {len(members_to_greet)} members.")
            except Exception as e:
                logger.error(f"Failed to send daily greeting: {e}", exc_info=True)
                
    def check_monthly_anniversaries(self):
        """Task 2: Sends thank you messages for membership anniversaries."""
        group_id = Config.GROUP_CHAT_ID()
        if not group_id: return
        
        now = self._get_current_utc_time()
        now_date = now.date()
        
        members_to_thank = []
        for user_id, username, joined_date_str, _, last_thanked_month in self._get_all_active_members():
            if not joined_date_str: continue
            
            # Calculate month difference
            joined_date = datetime.strptime(joined_date_str.split()[0], '%Y-%m-%d').date()
            month_diff = (now_date.year - joined_date.year) * 12 + now_date.month - joined_date.month
            
            # Check if: (1) It's been 1 month or more, AND (2) Today is their join date,
            # AND (3) They haven't been thanked for this membership month yet.
            if month_diff > 0 and now_date.day == joined_date.day and month_diff > last_thanked_month:
                members_to_thank.append((user_id, username, month_diff))

        if members_to_thank:
            message_parts = []
            for user_id, username, months in members_to_thank:
                mention = f"[{username or 'Fren'}](tg://user?id={user_id})"
                # Use MEMBERSHIP_ANNIVERSARY (Task 2)
                thanks_message = random.choice(self.responses.get("MEMBERSHIP_ANNIVERSARY", [])).format(mention=mention, months=months)
                message_parts.append(thanks_message)
                
                # Update DB: Mark as thanked for this month
                self._update_member_info(user_id, last_thanked_month=months)

            final_message = "\n\n---\n\n".join(message_parts)
            try:
                self.bot.send_message(group_id, final_message, parse_mode="Markdown")
                logger.info(f"Sent membership anniversary greetings for {len(members_to_thank)} members.")
            except Exception as e:
                logger.error(f"Failed to send membership anniversary greetings: {e}")
                
    def ask_for_birthdays(self):
        """Task 3: Asks for birthdays weekly."""
        group_id = Config.GROUP_CHAT_ID()
        if not group_id: return
        
        # Use BIRTHDAY_ASK (Task 3)
        message = random.choice(self.responses.get("BIRTHDAY_ASK", []))
        try:
            self.bot.send_message(group_id, message, parse_mode="Markdown")
            logger.info("Sent weekly birthday question.")
        except Exception as e:
            logger.error(f"Failed to send birthday question: {e}")

    def send_scheduled_health_reminder(self):
        """Task 4: Reminds members to take care of their health 3x a day."""
        group_id = Config.GROUP_CHAT_ID()
        if not group_id: return
        
        # Get all valid members in the group to tag
        all_members = self._get_all_active_members()
        
        # Filter for active members
        valid_members = []
        for user_id, username, _, _, _ in all_members:
             try:
                chat_member = self.bot.get_chat_member(group_id, user_id)
                if chat_member.status in ['member', 'administrator', 'creator']:
                    valid_members.append((user_id, username))
             except:
                continue

        # Take 5 random tags (limited for API compliance/spam reduction)
        tags_to_use = random.sample(valid_members, min(5, len(valid_members)))
        tags_list = [f"[{username or 'Fren'}](tg://user?id={user_id})" for user_id, username in tags_to_use]
        tags_string = " ".join(tags_list) if tags_list else "Frens"

        # Use HEALTH_REMINDER (Task 4)
        message_template = random.choice(self.responses.get("HEALTH_REMINDER", []))
        
        final_message = f"🚨 **ATTENTION NPEPE ARMY** 🚨\n\n{message_template.format(tags=tags_string)}"

        try:
            self.bot.send_message(group_id, final_message, parse_mode="Markdown")
            logger.info("Sent scheduled health reminder.")
        except Exception as e:
            logger.error(f"Failed to send health reminder: {e}", exc_info=True)
            
    # --- AI RENEWAL FUNCTION ---

    def renew_responses_with_ai(self):
        logger.info("Starting weekly AI response renewal process.")
        if not self.groq_client:
            logger.warning("Skipping AI renewal: Groq not initialized.")
            return

        categories_to_renew = {
            # Task 1: Delayed New Member Greeting (50 variations)
            "GREET_NEW_MEMBERS_DELAYED": ("Produce 50 unique, friendly, and funny welcome messages for new members in a crypto group after a 5-minute delay. Must include the placeholder '{name}'. Use $NPEPE slang: 'fren', 'ribbit', 'HODL', 'based', 'LFG', 'WAGMI'.", 40),
            
            # Task 1: Daily Random Greeting (50 variations)
            "DAILY_GREETING": ("Produce 50 short, energetic, and based check-in messages for a crypto group bot. The message should tag a random user (using the placeholder '{mention}') and ask how they are HODLing up or how their day is going. Use $NPEPE slang: 'fren', 'ribbit', 'HODL', 'based', 'LFG', 'WAGMI'.", 40),

            # Task 2: Membership Anniversary Greeting (50 variations)
            "MEMBERSHIP_ANNIVERSARY": ("Produce 50 unique, proud, and enthusiastic messages to thank a member for their long-term HODLing in a meme coin community (1, 2, 3, etc., months). Must include placeholders '{mention}' for the user and '{months}' for the duration. Use strong $NPEPE/meme coin slang: 'diamond hands', 'based', 'LFG', 'WAGMI'.", 40),

            # Task 3: Birthday Question (30 variations)
            "BIRTHDAY_ASK": ("Produce 30 unique, fun, and casual messages for a meme coin bot to ask the community if anyone is having a birthday this week. Use $NPEPE slang: 'fren', 'ribbit', 'HODL', 'based'.", 25),
            
            # Task 3: Birthday Greeting (30 variations)
            "BIRTHDAY_GREETING": ("Produce 30 unique, energetic, and funny birthday greetings for a meme coin member. Use the placeholder '{name}' and $NPEPE slang: 'fren', 'ribbit', 'HODL', 'based', 'LFG', 'WAGMI'.", 25),

            # Task 4: Health Reminder (100 variations)
            "HEALTH_REMINDER": ("Produce 100 unique, caring, but meme-themed messages reminding all crypto members to take a break, eat, rest, and spend time with loved ones, to prevent burnout from trading/HODLing. Must include the placeholder '{tags}' for a list of mentions. Use $NPEPE slang: 'fren', 'diamond hands', 'HODL'.", 80),
            
            # Last Task: Bot Identity Answer (20 variations)
            "BOT_IDENTITY": ("Produce 20 short, chaotic, and proud identity answers for a meme coin bot. The bot should explicitly state it is a 'frog' whose task is to keep members 'sane' while they HODL $NPEPE. Use $NPEPE slang: 'fren', 'ribbit', 'based'.", 15),
            
            # Retain COLLABORATION_RESPONSE for existing functionality
            "COLLABORATION_RESPONSE": ("Produce 20 enthusiastic responses for a meme coin bot when someone asks about collaboration or marketing. Focus on community effort (raids, memes) over paid promotion. Use slang.", 15),
        }
        
        for category, (prompt, min_count) in categories_to_renew.items():
            try:
                logger.info(f"Requesting AI update for category: {category}...")
                completion = self.groq_client.chat.completions.create(
                    messages=[{"role": "system", "content": prompt}],
                    model="llama3-8b-8192", temperature=1.0, max_tokens=2000
                )
                text = completion.choices[0].message.content
                new_lines = [line.strip() for line in re.split(r'\n|\d+\.', text) if line.strip() and len(line) > 5]
                
                # Filter for required placeholders
                placeholder_map = {
                    "GREET_NEW_MEMBERS_DELAYED": '{name}', 
                    "DAILY_GREETING": '{mention}', 
                    "MEMBERSHIP_ANNIVERSARY": '{mention}', 
                    "BIRTHDAY_GREETING": '{name}', 
                    "HEALTH_REMINDER": '{tags}'
                }
                
                placeholder = placeholder_map.get(category)
                if placeholder:
                    new_lines = [line for line in new_lines if placeholder in line]
                    
                if category == "MEMBERSHIP_ANNIVERSARY":
                    new_lines = [line for line in new_lines if '{months}' in line]

                if len(new_lines) >= min_count:
                    self.responses[category] = new_lines
                    logger.info(f" ✅  Category '{category}' successfully updated by AI with {len(new_lines)} new entries.")
                else:
                    logger.warning(f" ⚠️  AI update for '{category}' only produced {len(new_lines)} lines (needed {min_count}); update skipped.")
            except Exception as e:
                logger.error(f" ❌  Failed to update category '{category}' with AI: {e}", exc_info=True)
