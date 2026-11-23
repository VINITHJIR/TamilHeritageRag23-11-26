-- =====================================================================
-- Sample data: users, chat history, question types, citations, feedback, logs
-- Run AFTER load_vectors.py (citations point to real chunk_registry rows)
-- Users are fictional test accounts.
-- =====================================================================

INSERT INTO question_types (code, label_en, label_ta, description) VALUES
 ('history',         'History',              'வரலாறு',             'Who built it, when, dynasty, events'),
 ('architecture',    'Architecture',         'கட்டடக்கலை',         'Style, structure, towers, carvings'),
 ('location',        'Location',             'இருப்பிடம்',          'Where is it, which district/taluk'),
 ('how_to_reach',    'How to reach',         'செல்லும் வழி',       'Bus, train, road, distance'),
 ('timings_entry',   'Timings & entry',      'நேரம் & கட்டணம்',     'Opening hours, tickets, dress code'),
 ('festival',        'Festivals',            'திருவிழாக்கள்',       'Festivals, rituals, special days'),
 ('recommendation',  'Recommendation',       'பரிந்துரை',          'Places to visit / where can I see X'),
 ('comparison',      'Comparison',           'ஒப்பீடு',            'Compare two or more sites'),
 ('general_heritage','General heritage',     'பொது பாரம்பரியம்',   'Concepts not tied to one site'),
 ('out_of_scope',    'Out of scope',         'தொடர்பில்லாதது',      'Not about heritage -> rejected');

-- ---------------- USERS ----------------
INSERT INTO users (user_id, auth_provider, email, phone, display_name, preferred_lang,
                   home_district_id, user_type, role, consent_logging, last_active_at) VALUES
 ('11111111-1111-4111-8111-111111111111','google',   'priya.student@example.com', NULL,        'Priya',      'ta',      6, 'student',   'user',     TRUE,  '2026-11-21 10:05+05:30'),
 ('22222222-2222-4222-8222-222222222222','phone_otp', NULL,                       '+91900000002','Ravi',      'kn',      NULL,'tourist', 'user',     TRUE,  '2026-11-21 18:40+05:30'),
 ('33333333-3333-4333-8333-333333333333','guest',     NULL,                       NULL,        NULL,         'ta-Latn', NULL,'other',   'user',     FALSE, '2026-11-21 12:30+05:30'),
 ('44444444-4444-4444-8444-444444444444','email',     'meena.research@example.com',NULL,       'Dr. Meena',  'en',      8, 'researcher','user',     TRUE,  '2026-11-21 16:20+05:30'),
 ('55555555-5555-4555-8555-555555555555','google',    'reviewer@example.com',     NULL,        'HR Reviewer','en',      NULL,NULL,      'reviewer', TRUE,  '2026-11-21 20:00+05:30');

-- ---------------- CHAT SESSIONS ----------------
INSERT INTO chat_sessions (session_id, user_id, title, client, started_at, last_message_at) VALUES
 ('a0000000-0000-4000-8000-000000000001','11111111-1111-4111-8111-111111111111','தஞ்சை பெரிய கோயில்','android','2026-11-21 10:02+05:30','2026-11-21 10:05+05:30'),
 ('a0000000-0000-4000-8000-000000000002','22222222-2222-4222-8222-222222222222','ಬನ್ನಾರಿ ಅಮ್ಮನ್','web',    '2026-11-20 09:10+05:30','2026-11-20 09:10+05:30'),
 ('a0000000-0000-4000-8000-000000000003','33333333-3333-4333-8333-333333333333','bannari festival','web',      '2026-11-21 12:30+05:30','2026-11-21 12:30+05:30'),
 ('a0000000-0000-4000-8000-000000000004','44444444-4444-4444-8444-444444444444','Chola bronzes & Keeladi','web','2026-11-21 16:00+05:30','2026-11-21 16:20+05:30'),
 ('a0000000-0000-4000-8000-000000000005','22222222-2222-4222-8222-222222222222','Kanyakumari forts','android','2026-11-21 18:38+05:30','2026-11-21 18:40+05:30');

-- ---------------- CHAT MESSAGES (history) ----------------
INSERT INTO chat_messages (message_id, session_id, role, content, lang, question_type, resolved_site, reply_to, created_at) VALUES
 (1, 'a0000000-0000-4000-8000-000000000001','user',      'தஞ்சை பெரிய கோயிலை கட்டியவர் யார்?','ta','history','TN-TNJ-0001',NULL,'2026-11-21 10:02+05:30'),
 (2, 'a0000000-0000-4000-8000-000000000001','assistant', 'தஞ்சை பெரிய கோயிலை முதலாம் இராஜராஜ சோழன் கட்டினார். இது கி.பி. 1010 அளவில் கட்டி முடிக்கப்பட்டது.','ta',NULL,'TN-TNJ-0001',1,'2026-11-21 10:02+05:30'),
 (3, 'a0000000-0000-4000-8000-000000000001','user',      'அங்கு நுழைவுக் கட்டணம் உண்டா?','ta','timings_entry','TN-TNJ-0001',NULL,'2026-11-21 10:05+05:30'),
 (4, 'a0000000-0000-4000-8000-000000000001','assistant', 'இல்லை. தஞ்சை பெரிய கோயிலுக்குள் நுழைய கட்டணம் இல்லை.','ta',NULL,'TN-TNJ-0001',3,'2026-11-21 10:05+05:30'),
 (5, 'a0000000-0000-4000-8000-000000000002','user',      'ಬನ್ನಾರಿ ಅಮ್ಮನ್ ದೇವಸ್ಥಾನ ಎಲ್ಲಿದೆ?','kn','location','TN-ERD-0001',NULL,'2026-11-20 09:10+05:30'),
 (6, 'a0000000-0000-4000-8000-000000000002','assistant', 'ಬನ್ನಾರಿ ಅಮ್ಮನ್ ದೇವಸ್ಥಾನವು ತಮಿಳುನಾಡಿನ ಈರೋಡ್ ಜಿಲ್ಲೆಯ ಸತ್ಯಮಂಗಲದ ಬಳಿ, ಮೈಸೂರು ರಸ್ತೆಯಲ್ಲಿ ಇದೆ.','kn',NULL,'TN-ERD-0001',5,'2026-11-20 09:10+05:30'),
 (7, 'a0000000-0000-4000-8000-000000000003','user',      'bannari amman kovil la enna festival nadakkum','ta-Latn','festival','TN-ERD-0001',NULL,'2026-11-21 12:30+05:30'),
 (8, 'a0000000-0000-4000-8000-000000000003','assistant', 'Bannari Amman kovil-la Panguni maasam Kundam thiruvizha nadakkum. Bakthargal theekundathil nadandhu nerthikadan seluthuvaanga.','ta-Latn',NULL,'TN-ERD-0001',7,'2026-11-21 12:30+05:30'),
 (9, 'a0000000-0000-4000-8000-000000000004','user',      'Where can I see Chola bronze statues in Tamil Nadu?','en','recommendation','TN-CHN-0001',NULL,'2026-11-21 16:00+05:30'),
 (10,'a0000000-0000-4000-8000-000000000004','assistant', 'The Bronze Gallery of the Government Museum in Egmore, Chennai has one of the finest collections of Chola bronzes, including Nataraja images.','en',NULL,'TN-CHN-0001',9,'2026-11-21 16:00+05:30'),
 (11,'a0000000-0000-4000-8000-000000000004','user',      'When did the Keeladi excavation start and who started it?','en','history','TN-SVG-0001',NULL,'2026-11-21 16:10+05:30'),
 (12,'a0000000-0000-4000-8000-000000000004','assistant', 'Excavation at Keeladi began in 2015 under the Archaeological Survey of India. The Tamil Nadu State Department of Archaeology continued it later.','en',NULL,'TN-SVG-0001',11,'2026-11-21 16:10+05:30'),
 (13,'a0000000-0000-4000-8000-000000000004','user',      'Tell me about Sri Kaviyan Temple in Erode','en','history',NULL,NULL,'2026-11-21 16:20+05:30'),
 (14,'a0000000-0000-4000-8000-000000000004','assistant', 'I could not find any reliable record of a temple by that name in Erode district, so I will not guess. Could you check the spelling or share the village name?','en',NULL,NULL,13,'2026-11-21 16:20+05:30'),
 (15,'a0000000-0000-4000-8000-000000000005','user',      'Which forts can I visit near Kanyakumari?','en','recommendation','TN-KNK-0001',NULL,'2026-11-21 18:38+05:30'),
 (16,'a0000000-0000-4000-8000-000000000005','assistant', 'Vattakottai Fort, an 18th-century granite seaside fort built under the Travancore kingdom, is close to Kanniyakumari.','en',NULL,'TN-KNK-0001',15,'2026-11-21 18:40+05:30');
SELECT setval('chat_messages_message_id_seq', 16);

-- ---------------- CITATIONS (real chunk ids from chunk_registry) ----------------
INSERT INTO message_citations (message_id, chunk_id, rank, score)
SELECT v.mid, r.chunk_id, v.rk, v.sc
FROM (VALUES
  (2,'TN-TNJ-0001','history','ta',1,0.711), (2,'TN-TNJ-0001','overview','en',2,0.576),
  (4,'TN-TNJ-0001','visitor_info','ta',1,0.640),
  (6,'TN-ERD-0001','overview','kn',1,0.704), (6,'TN-ERD-0001','overview','ta',2,0.597), (6,'TN-ERD-0001','how_to_reach','en',3,0.573),
  (8,'TN-ERD-0001','festivals','ta',1,0.465),
  (10,'TN-CHN-0001','collections','en',1,0.591),
  (12,'TN-SVG-0001','history','ta',1,0.650), (12,'TN-SVG-0001','overview','en',2,0.600),
  (16,'TN-KNK-0001','overview','en',1,0.560)
) AS v(mid, site, sec, lang, rk, sc)
JOIN chunk_registry r ON r.site_id = v.site AND r.section = v.sec AND r.lang = v.lang;

-- ---------------- FEEDBACK ----------------
INSERT INTO message_feedback (message_id, user_id, rating, reason, comment) VALUES
 (2,  '11111111-1111-4111-8111-111111111111', 1, NULL, 'சரியான பதில்'),
 (8,  '33333333-3333-4333-8333-333333333333', 1, NULL, 'super, Tanglish-laye answer vandhuchu'),
 (14, '44444444-4444-4444-8444-444444444444', 1, NULL, 'Good that it did not invent a fake temple'),
 (16, '22222222-2222-4222-8222-222222222222',-1, 'incomplete', 'Only one fort was suggested');

-- ---------------- QUERY LOGS (pipeline trace per user message) ----------------
INSERT INTO query_logs (message_id, query_text, query_lang, rewritten_en, resolved_site, path, top_score, latency_ms, chunk_ids)
SELECT m.message_id, m.content, m.lang, v.rw, m.resolved_site, v.path, v.top, v.lat,
       (SELECT array_agg(c.chunk_id ORDER BY c.rank) FROM message_citations c WHERE c.message_id = m.message_id + 1)
FROM chat_messages m
JOIN (VALUES
  (1, 'Who built the Thanjavur Big Temple?',            'db',       0.711, 1840),
  (3, 'Is there an entry fee at the Big Temple?',       'db',       0.640, 1210),
  (5, 'Where is Bannari Amman Temple?',                 'db',       0.704, 1650),
  (7, 'What festival happens at Bannari Amman temple?', 'db',       0.465, 2105),
  (9, 'Where can I see Chola bronze statues?',          'db',       0.591, 1530),
  (11,'When and by whom was Keeladi excavation started?','db',      0.650, 1720),
  (13,'Sri Kaviyan Temple Erode',                       'rejected', 0.212, 6400),
  (15,'Forts to visit near Kanyakumari',                'db',       0.560, 1480)
) AS v(mid, rw, path, top, lat) ON v.mid = m.message_id;
