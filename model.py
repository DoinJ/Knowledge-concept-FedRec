import tensorflow as tf

from keras.layers import *
from keras.models import Model
from keras import backend as K
from keras.engine.topology import Layer, InputSpec
from keras import initializers   
from keras.optimizers import *
from const import *
import keras

class GraphConvolutionLayer(tf.keras.layers.Layer):
    """Basic graph convolution layer as in https://arxiv.org/abs/1609.02907"""
    def __init__(self, input_dim, output_dim, support=1,
                 activation=None,
                 use_bias=True,
                 kernel_initializer='glorot_uniform',
                 bias_initializer='zeros',
                 kernel_regularizer=None,
                 bias_regularizer=None):
        super(GraphConvolutionLayer, self).__init__()

        self.input_dim = input_dim
        self.output_dim = output_dim
        self.use_bias = use_bias
        self.kernel_initializer = kernel_initializer
        self.bias_initializer = bias_initializer
        self.kernel_regularizer = kernel_regularizer
        self.bias_regularizer = bias_regularizer
        self.activation = activation

    def build(self, nodes_shape):
        self.kernel = self.add_weight(shape = (self.input_dim, self.output_dim),
                                      initializer = self.kernel_initializer,
                                      name = 'kernel',
                                      regularizer = self.kernel_regularizer)
        if self.use_bias:
            self.bias = self.add_weight(shape=(self.output_dim, ),
                                        initializer=self.bias_initializer,
                                        name='bias',
                                        regularizer = self.bias_regularizer)
        else:
            self.bias = None
            
        self.built = True

    def call(self, nodes, edges):
        support = tf.matmul(nodes, self.kernel) 

        output = tf.matmul(edges, support)

        if self.use_bias:
            output += self.bias

        if self.activation is not None:
            output = self.activation(output)
            
        return output


class GraphConvolutionModel(tf.keras.Model):
    def __init__(self):
        super(GraphConvolutionModel, self).__init__()

        self.graph_conv_1 = GraphConvolutionLayer(1433, 16,
                    activation=tf.keras.activations.relu,
                    kernel_regularizer=tf.keras.regularizers.l2(0.01))

        self.graph_conv_2 = GraphConvolutionLayer(16, 7)

    def call(self, x, training=False):

        nodes = x[0]
        edges = x[1]

        h = self.graph_conv_1(nodes, edges)
        logit = self.graph_conv_2(h, edges)

        return logit


class ComputeMasking(keras.layers.Layer):
    def __init__(self, maskvalue=0,**kwargs):
        self.maskvalue=maskvalue
        super(ComputeMasking, self).__init__(**kwargs)

    def call(self, inputs, **kwargs):
        mask = K.not_equal(inputs, self.maskvalue)
        return K.cast(mask, K.floatx())*(-99)

    def compute_output_shape(self, input_shape):
        return input_shape

def get_model(Otraining,hidden=HIDDEN,dropout=DROP):

    userembedding_layer = Embedding(Otraining.shape[0]+3, hidden, trainable=True)
    itemembedding_layer = Embedding(Otraining.shape[1]+3, hidden, trainable=True)

    userid_input = Input(shape=(1,), dtype='int32')
    itemid_input = Input(shape=(1,), dtype='int32')
    
    ui_input = Input(shape=(HIS_LEN,), dtype='int32')
    neighbor_embedding_input = Input(shape=(HIS_LEN,NEIGHBOR_LEN,hidden), dtype='float32')
    mask_neighbor = Lambda(lambda x:K.cast(K.cast(K.sum(x,axis=-1),'bool'),'float32'))(neighbor_embedding_input)
    
    neighbor_embeddings = TimeDistributed(TimeDistributed(Dense(hidden)))(neighbor_embedding_input)

    uiemb = Dense(hidden,activation='sigmoid')(itemembedding_layer(ui_input))
    uiembrepeat = Lambda(lambda x :K.repeat_elements(K.expand_dims(x,axis=2),NEIGHBOR_LEN,axis=2))(uiemb) 
    attention_gat = Reshape((HIS_LEN,NEIGHBOR_LEN))(LeakyReLU()(TimeDistributed(TimeDistributed(Dense(1)))(concatenate([uiembrepeat,neighbor_embeddings]))))
    attention_gat = Lambda(lambda x:x[0]+(1-x[1])*(-99))([attention_gat,mask_neighbor])
    agg_neighbor_embeddings = Lambda(lambda x:K.sum(K.repeat_elements(K.expand_dims(x[0],axis=3),hidden,axis=3)*x[1],axis=-2))([attention_gat,neighbor_embeddings])
    
    uiemb_agg = Dense(hidden)(concatenate([agg_neighbor_embeddings,uiemb]))  
    uemb = Dense(hidden,activation='sigmoid')(Flatten()(userembedding_layer(userid_input)))
    uemb = Dropout(dropout)(uemb)
    iemb = Dense(hidden,activation='sigmoid')(Flatten()(itemembedding_layer(itemid_input)))
    iemb = Dropout(dropout)(iemb)
    
    masker = ComputeMasking(Otraining.shape[1]+2)(ui_input)
    uembrepeat = Lambda(lambda x :K.repeat_elements(K.expand_dims(x,axis=1),HIS_LEN,axis=1))(uemb) 

    attention = Flatten()(LeakyReLU()(Dense(1)(concatenate([uembrepeat,uiemb_agg]))))
    attention = add([attention,masker])
    attention_weight = Activation('softmax')(attention)
    uemb_g = Dot((1, 1))([uiemb, attention_weight])
    uemb_g = Dense(hidden)(concatenate([uemb_g, uemb]))

    out = Dense(1,activation='sigmoid')(concatenate([uemb_g, iemb]))
    model = Model([userid_input,itemid_input,ui_input,neighbor_embedding_input],out)
    model.compile(loss=['mse'], optimizer=SGD(lr=LR,clipnorm=CLIP), metrics=['mse'])
    return model,userembedding_layer,itemembedding_layer