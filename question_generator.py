import random

def generate_questions(text):

    questions=[

    {
    "question":"What does Artificial Intelligence simulate?",
    "options":[
    "Human intelligence",
    "Animal instincts",
    "Weather patterns",
    "Electric current"
    ],
    "answer":"Human intelligence",
    "difficulty":"easy"
    },

    {
    "question":"Which algorithm is used for classification?",
    "options":[
    "Linear Regression",
    "Logistic Regression",
    "K Means",
    "Apriori"
    ],
    "answer":"Logistic Regression",
    "difficulty":"medium"
    },

    {
    "question":"Which field focuses on training machines from data?",
    "options":[
    "Machine Learning",
    "Computer Graphics",
    "Networking",
    "Operating Systems"
    ],
    "answer":"Machine Learning",
    "difficulty":"easy"
    },

    {
    "question":"Which technique reduces overfitting?",
    "options":[
    "Regularization",
    "Sorting",
    "Compilation",
    "Indexing"
    ],
    "answer":"Regularization",
    "difficulty":"hard"
    }

    ]

    random.shuffle(questions)

    return questions