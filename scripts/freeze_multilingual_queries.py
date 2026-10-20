"""Freeze paper-grounded query proposals before retrieval calibration.

Questions are agent-authored, not human/native-speaker judgments. Source
paragraphs are verified against existing immutable PDFs. BERT is held out by
source document; same-paper versions are not split across development/holdout.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import unicodedata

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = ['en', 'zh', 'de', 'fr', 'es', 'ja', 'ko', 'ar']
# ID, file, physical page, exact normalized source anchor, questions in the
# declared language order. Expected facts are fixed before observing scores.
ROWS = [
('T1','1706.03762v7.pdf',3,'The encoder is composed of a stack of N = 6 identical layers.',[
'How many identical layers make up the Transformer encoder?', 'Transformer编码器由多少个相同层组成？',
'Aus wie vielen identischen Schichten besteht der Transformer-Encoder?', 'Combien de couches identiques composent l’encodeur du Transformer ?',
'¿Cuántas capas idénticas forman el codificador del Transformer?', 'Transformerのエンコーダは同一の層を何層重ねていますか？',
'Transformer 인코더는 동일한 층을 몇 개 쌓습니까?', 'من كم طبقة متطابقة يتكون مشفر Transformer؟']),
('T2','1706.03762v7.pdf',3,'prevent positions from attending to subsequent positions',[
'Why does Transformer decoder self-attention mask subsequent positions?', 'Transformer解码器的自注意力为什么要屏蔽后续位置？',
'Warum maskiert die Selbstaufmerksamkeit des Transformer-Decoders spätere Positionen?', 'Pourquoi l’auto-attention du décodeur Transformer masque-t-elle les positions suivantes ?',
'¿Por qué la autoatención del decodificador Transformer oculta las posiciones posteriores?', 'Transformerのデコーダの自己注意は、なぜ後続位置をマスクしますか？',
'Transformer 디코더의 자기 주의는 왜 이후 위치를 가립니까?', 'لماذا يحجب الانتباه الذاتي في مفكك Transformer المواضع اللاحقة؟']),
('T3','1706.03762v7.pdf',5,'h = 8 parallel attention layers',[
'How many parallel attention heads are used in this Transformer?', '论文中的Transformer使用多少个并行注意力头？',
'Wie viele parallele Aufmerksamkeitsköpfe verwendet dieser Transformer?', 'Combien de têtes d’attention parallèles ce Transformer utilise-t-il ?',
'¿Cuántas cabezas de atención paralelas utiliza este Transformer?', 'このTransformerは並列の注意ヘッドをいくつ使いますか？',
'이 Transformer는 병렬 주의 헤드를 몇 개 사용합니까?', 'كم رأس انتباه متوازيا يستخدم نموذج Transformer في الورقة؟']),
('T4','1706.03762v7.pdf',5,'the inner-layer has dimensionality dff = 2048',[
'What is the inner-layer dimension of the Transformer feed-forward network?', 'Transformer前馈网络的内部层维度是多少？',
'Welche Dimension hat die innere Schicht des Feedforward-Netzes im Transformer?', 'Quelle est la dimension interne du réseau feed-forward du Transformer ?',
'¿Cuál es la dimensión interna de la red de avance del Transformer?', 'Transformerのフィードフォワードネットワークの中間層の次元はいくつですか？',
'Transformer 순방향 네트워크의 내부 층 차원은 얼마입니까?', 'ما بُعد الطبقة الداخلية للشبكة الأمامية في Transformer؟']),
('T5','1706.03762v7.pdf',6,'We chose the sinusoidal version because',[
'Why did the Transformer authors choose sinusoidal instead of learned positional embeddings?', 'Transformer作者为什么选择正弦位置编码而非可学习的位置嵌入？',
'Warum wählten die Transformer-Autoren sinusförmige statt gelernter Positionskodierungen?', 'Pourquoi les auteurs du Transformer ont-ils choisi des positions sinusoïdales plutôt qu’apprises ?',
'¿Por qué eligieron los autores del Transformer posiciones sinusoidales en vez de aprendidas?', 'Transformerの著者は、学習する位置埋め込みではなく正弦波版をなぜ選びましたか？',
'Transformer 저자들은 왜 학습된 위치 임베딩 대신 사인파 방식을 선택했습니까?', 'لماذا اختار مؤلفو Transformer الترميز الموضعي الجيبي بدلا من التضمينات المتعلمة؟']),
('T6','1706.03762v7.pdf',7,'We used warmup_steps = 4000',[
'How many warmup steps does the Transformer learning-rate schedule use?', 'Transformer学习率调度使用多少个预热步？',
'Wie viele Aufwärmschritte verwendet der Lernratenplan des Transformers?', 'Combien d’étapes de préchauffage le calendrier du taux d’apprentissage du Transformer utilise-t-il ?',
'¿Cuántos pasos de calentamiento usa el programa de tasa de aprendizaje del Transformer?', 'Transformerの学習率スケジュールのウォームアップは何ステップですか？',
'Transformer 학습률 일정의 워밍업 단계는 몇 번입니까?', 'كم خطوة إحماء يستخدم جدول معدل التعلم في Transformer؟']),
('T7','1706.03762v7.pdf',8,'For the base model, we use a rate of Pdrop = 0.1',[
'What dropout rate is used for the base Transformer model?', 'Transformer基础模型使用的dropout比例是多少？',
'Welche Dropout-Rate verwendet das Transformer-Basismodell?', 'Quel taux de dropout est utilisé pour le modèle Transformer de base ?',
'¿Qué tasa de dropout usa el modelo Transformer base?', 'Transformerの基本モデルのドロップアウト率はいくつですか？',
'Transformer 기본 모델의 드롭아웃 비율은 얼마입니까?', 'ما معدل الإسقاط المستخدم في نموذج Transformer الأساسي؟']),
('T8','1706.03762v7.pdf',8,'we employed label smoothing',[
'What label-smoothing value is used when training the Transformer?', 'Transformer训练时使用的标签平滑系数是多少？',
'Welcher Wert für Label Smoothing wird beim Transformer-Training verwendet?', 'Quelle valeur de lissage des étiquettes est utilisée pour entraîner le Transformer ?',
'¿Qué valor de suavizado de etiquetas se usa al entrenar el Transformer?', 'Transformerの学習で使うラベルスムージングの値はいくつですか？',
'Transformer 학습의 레이블 스무딩 값은 얼마입니까?', 'ما قيمة تنعيم التسميات المستخدمة أثناء تدريب Transformer؟']),
('R1','2005.11401v4.pdf',3,'The retrieval component',[
'Which retrieval architecture is used by RAG?', 'RAG的检索组件采用什么架构？',
'Welche Abrufarchitektur verwendet RAG?', 'Quelle architecture de recherche RAG utilise-t-il ?',
'¿Qué arquitectura de recuperación utiliza RAG?', 'RAGの検索コンポーネントはどのアーキテクチャを使いますか？',
'RAG 검색 구성 요소는 어떤 구조를 사용합니까?', 'ما بنية الاسترجاع التي يستخدمها RAG؟']),
('R2','2005.11401v4.pdf',3,'We use BART-large',[
'Which pretrained generator and parameter count are used in RAG?', 'RAG采用哪个预训练生成器，它有多少参数？',
'Welchen vortrainierten Generator mit wie vielen Parametern verwendet RAG?', 'Quel générateur préentraîné RAG utilise-t-il et combien de paramètres possède-t-il ?',
'¿Qué generador preentrenado utiliza RAG y cuántos parámetros tiene?', 'RAGはどの事前学習済み生成器を使い、そのパラメータ数はいくつですか？',
'RAG는 어떤 사전 학습 생성기를 사용하며 매개변수 수는 얼마입니까?', 'ما المولد المدرب مسبقا الذي يستخدمه RAG وكم عدد معاملاته؟']),
('R3','2005.11401v4.pdf',4,'keep the document encoder (and index) fixed',[
'Which RAG components are kept fixed and which are fine-tuned during training?', 'RAG训练中哪些组件保持固定，哪些组件参与微调？',
'Welche RAG-Komponenten bleiben beim Training fest und welche werden feinabgestimmt?', 'Quels composants de RAG restent fixes et lesquels sont ajustés pendant l’entraînement ?',
'¿Qué componentes de RAG permanecen fijos y cuáles se ajustan durante el entrenamiento?', 'RAGの学習では、どの構成要素を固定し、どれを微調整しますか？',
'RAG 학습에서 어떤 구성 요소를 고정하고 어떤 것을 미세 조정합니까?', 'ما مكونات RAG التي تبقى ثابتة وأيها يتم ضبطه أثناء التدريب؟']),
('R4','2005.11401v4.pdf',4,'we use the December 2018 dump',[
'Which Wikipedia snapshot, chunk size and document count are used by RAG?', 'RAG使用哪一版维基百科快照，按多少词切块，共得到多少文档？',
'Welchen Wikipedia-Stand, welche Abschnittslänge und wie viele Dokumente verwendet RAG?', 'Quelle version de Wikipédia, quelle taille de passage et combien de documents RAG utilise-t-il ?',
'¿Qué versión de Wikipedia, tamaño de fragmento y número de documentos utiliza RAG?', 'RAGは何年何月のWikipediaを使い、何語ずつ分割して何件の文書を作りますか？',
'RAG는 어느 시점의 위키백과를 몇 단어씩 나누어 몇 개 문서를 만듭니까?', 'ما نسخة ويكيبيديا وحجم المقاطع وعدد الوثائق التي يستخدمها RAG؟']),
('B1','1810.04805v2.pdf',3,'We primarily report results on two model sizes',[
'What are the layer, hidden-size and attention-head configurations of BERT Base and Large?', 'BERT Base和Large的层数、隐藏维度与注意力头数分别是多少？',
'Welche Schichtzahlen, versteckten Dimensionen und Kopfzahlen haben BERT Base und Large?', 'Quels nombres de couches, dimensions cachées et têtes ont BERT Base et Large ?',
'¿Cuáles son las capas, dimensiones ocultas y cabezas de BERT Base y Large?', 'BERT BaseとLargeの層数、隠れ次元、注意ヘッド数はそれぞれいくつですか？',
'BERT Base와 Large의 층 수, 은닉 차원, 주의 헤드 수는 각각 얼마입니까?', 'ما عدد الطبقات والأبعاد المخفية ورؤوس الانتباه في BERT Base وLarge؟']),
('B2','1810.04805v2.pdf',4,'with a 30,000 token vocabulary',[
'Which tokenization method and vocabulary size are used by BERT?', 'BERT使用哪种分词方法，词表大小是多少？',
'Welche Tokenisierung und Vokabulargröße verwendet BERT?', 'Quelle tokenisation et quelle taille de vocabulaire BERT utilise-t-il ?',
'¿Qué tokenización y tamaño de vocabulario utiliza BERT?', 'BERTはどの分割方法を使い、語彙数はいくつですか？',
'BERT는 어떤 토큰화 방식과 어휘 크기를 사용합니까?', 'ما طريقة تجزئة الكلمات وحجم المفردات المستخدمان في BERT؟']),
('B3','1810.04805v2.pdf',4,'hidden state corresponding to this token',[
'How is the final hidden state of BERT’s CLS token used?', 'BERT的CLS标记的最终隐藏状态用来做什么？',
'Wofür wird der letzte versteckte Zustand des CLS-Tokens von BERT verwendet?', 'À quoi sert l’état caché final du jeton CLS de BERT ?',
'¿Para qué se utiliza el estado oculto final del token CLS de BERT?', 'BERTのCLSトークンの最終隠れ状態は何に使われますか？',
'BERT CLS 토큰의 최종 은닉 상태는 무엇에 사용됩니까?', 'فيم تستخدم الحالة المخفية النهائية لرمز CLS في BERT؟']),
('B4','1810.04805v2.pdf',4,'we mask 15% of all WordPiece',[
'What proportion of WordPiece tokens is selected for BERT masked language modeling?', 'BERT掩码语言建模选择多少比例的WordPiece标记？',
'Welcher Anteil der WordPiece-Token wird für BERTs maskiertes Sprachmodell ausgewählt?', 'Quelle proportion des jetons WordPiece est sélectionnée pour le langage masqué de BERT ?',
'¿Qué proporción de tokens WordPiece se selecciona para el modelado enmascarado de BERT?', 'BERTのマスク言語モデルではWordPieceトークンの何割を選択しますか？',
'BERT 마스크 언어 모델은 WordPiece 토큰의 몇 퍼센트를 선택합니까?', 'ما نسبة رموز WordPiece التي يتم اختيارها للنمذجة المقنعة في BERT؟']),
('B5','1810.04805v2.pdf',4,'the [MASK] token 80%',[
'How are BERT’s selected prediction positions replaced with MASK, random or unchanged tokens?', 'BERT选中预测的位置分别按什么比例替换为MASK、随机词或保持原词？',
'Wie verteilt BERT gewählte Vorhersagepositionen auf MASK, zufällige und unveränderte Token?', 'Comment BERT répartit-il les positions choisies entre MASK, jetons aléatoires et jetons inchangés ?',
'¿Cómo reparte BERT las posiciones elegidas entre MASK, tokens aleatorios y tokens sin cambios?', 'BERTで予測対象に選んだ位置は、MASK、ランダム、変更なしにどう割り振られますか？',
'BERT의 예측 대상 위치는 MASK, 무작위 토큰, 원래 토큰으로 어떻게 나뉩니까?', 'كيف يوزع BERT مواضع التنبؤ المختارة بين MASK والرموز العشوائية وغير المعدلة؟']),
('B6','1810.04805v2.pdf',4,'50% of the time B is the actual',[
'How are positive and random sentence pairs sampled for BERT next-sentence prediction?', 'BERT下一句预测中真实后续句和随机句分别按什么比例采样？',
'Wie werden echte und zufällige Satzpaare für BERTs Vorhersage des nächsten Satzes gezogen?', 'Comment les paires réelles et aléatoires sont-elles échantillonnées pour la prédiction de phrase suivante de BERT ?',
'¿Cómo se muestrean los pares reales y aleatorios para predecir la siguiente oración en BERT?', 'BERTの次文予測では、本当の次の文とランダムな文をどの割合で選びますか？',
'BERT 다음 문장 예측에서 실제 다음 문장과 무작위 문장은 각각 어떤 비율로 선택합니까?', 'كيف تؤخذ عينات الجمل التالية الحقيقية والعشوائية في مهمة BERT لتوقع الجملة التالية؟']),
('B7','1810.04805v2.pdf',5,'BooksCorpus (800M words)',[
'Which corpora and word counts are used to pretrain BERT?', 'BERT预训练使用哪些语料库，各有多少词？',
'Welche Korpora mit welchen Wortzahlen werden für BERTs Vortraining verwendet?', 'Quels corpus et nombres de mots sont utilisés pour préentraîner BERT ?',
'¿Qué corpus y cantidades de palabras se utilizan para preentrenar BERT?', 'BERTの事前学習に使うコーパスと、それぞれの語数はいくつですか？',
'BERT 사전 학습에 사용하는 말뭉치와 각각의 단어 수는 얼마입니까?', 'ما مجموعات النصوص وأعداد كلماتها المستخدمة في التدريب المسبق لـBERT؟']),
('B8','1810.04805v2.pdf',4,'we only predict the masked words',[
'Does BERT masked language modeling reconstruct the whole input or predict only masked words?', 'BERT掩码语言建模是重构整个输入，还是只预测被遮蔽的词？',
'Rekonstruiert BERTs maskiertes Sprachmodell die gesamte Eingabe oder nur maskierte Wörter?', 'Le langage masqué de BERT reconstruit-il toute l’entrée ou prédit-il seulement les mots masqués ?',
'¿El modelado enmascarado de BERT reconstruye toda la entrada o predice solo las palabras ocultas?', 'BERTのマスク言語モデルは入力全体を復元しますか、それとも隠した単語だけを予測しますか？',
'BERT 마스크 언어 모델은 전체 입력을 복원합니까, 가려진 단어만 예측합니까?', 'هل تعيد نمذجة BERT المقنعة بناء الإدخال كله أم تتنبأ بالكلمات المخفية فقط؟']),
]


def normalized(text):
    return ' '.join(unicodedata.normalize('NFKC', text).split()).casefold()


def freeze():
    destination = ROOT / 'tests/fixtures/multilingual/20261020-v1'
    # An interrupted preflight may have made an empty folder. Never replace a
    # previously frozen manifest or any other saved file.
    if destination.exists():
        assert not any(destination.iterdir()), 'Preserve existing frozen data'
    sources, facts, queries = {}, [], []
    for name in sorted({row[1] for row in ROWS}):
        path = ROOT / 'data/samples' / name
        sources[name] = {'path': path.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
    for identity, name, page, anchor, translated in ROWS:
        assert len(translated) == len(LANGUAGES) and all(0 < len(q) <= 200 for q in translated), identity
        with pymupdf.open(ROOT / sources[name]['path']) as pdf:
            original = pdf[page-1]
            matches = [b for b in original.get_text('blocks') if b[6] == 0 and normalized(anchor) in normalized(b[4])]
            assert len(matches) == 1, (identity, anchor, len(matches))
            block = matches[0]
            evidence = {'physical_page': page, 'bbox': [block[0]/original.rect.width, block[1]/original.rect.height,
                block[2]/original.rect.width, block[3]/original.rect.height], 'native_text': block[4],
                'native_text_sha256': hashlib.sha256(block[4].encode()).hexdigest(), 'anchor': anchor}
        split = 'held_out_source' if identity.startswith('B') else 'development'
        facts.append({'id': identity, 'source': name, 'split': split, 'evidence': evidence})
        queries.extend({'id': identity+'-'+lang, 'fact_id': identity, 'language': lang, 'query': question, 'split': split}
                       for lang, question in zip(LANGUAGES, translated))
    manifest = {'version': '20261020-v1', 'created_at': datetime.now(timezone.utc).isoformat(),
                'sources': sources, 'facts': facts, 'queries': queries,
                'annotation': 'Agent-authored questions; original PDF paragraph anchors checked. No independent human/native-language validation claimed.',
                'split_policy': 'BERT source held out; Transformer and RAG development; no same-paper versions across splits.',
                'gates': {'recall_at_5_total': .9, 'recall_at_5_each_language': .8,
                          'max_english_regression_percentage_points': 5},
                'limitations': ['No evaluation performed while freezing.', 'Not official competition samples.',
                                '160 questions cover 20 facts; repeated source paragraphs are not 160 independent facts.',
                                'Unknown languages and real OCR quality remain outside this fixture.']}
    destination.mkdir(parents=True, exist_ok=True)
    with (destination/'manifest.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
    print(json.dumps({'path': str(destination), 'facts': len(facts), 'queries': len(queries),
                      'sha256': hashlib.sha256((destination/'manifest.json').read_bytes()).hexdigest()}), flush=True)


if __name__ == '__main__':
    freeze()
