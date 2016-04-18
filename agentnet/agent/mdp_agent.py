import lasagne                 
from lasagne.utils import unroll_scan
from theano import tensor as T
from ..utils import insert_dim

class MDPAgent:
    def __init__(self,
                 memory,
                 policy,
                 resolver,
                 input_map = 'default',
                ):
        """
        A generic agent within MDP abstraction,
            memory - memory.BaseAgentMemory child instance that
                - generates first (a-priori) agent state
                - determines new agent state given previous agent state and an observation
            policy - lasagne.Layer child instance that
                - determines Q-values or probabilities for all actions given current agent state and current observation,
                - can .get_output_for(hidden_state)
            resolver - resolver.BaseResolver child instance that
                - determines agent's action given Q-values for all actions
            input map - function(last_hidden,observation),
                that returns an input dictionary {input_layer:value} to be used for
                lasagne.get_output_for as a second param
                If 'default' is used, the input dictionary shall always be 
                memory.default_input_map result; by default:
                {
                    self.prev_state_input: last_state,
                    self.observation_input:observation,
                }
                where self is memory
        """        
        self.memory = memory
        self.policy = policy
        self.resolver = resolver
        if input_map =="default":
            input_map = memory.default_input_map
        self.input_map = input_map
        
    def get_agent_reaction(self,last_memory_state,observation,additional_outputs = [],**flags):
        """
        picks agent's action given:
            last_memory_state float[batch_id, memory_id]: agent's memory state on previous tick
            observation float[batch_id, input_id]: input observation at this tick
            additional_outputs: any other layers whose output you intend to track throughout the session (appended to the rest).
            flags: optional flags to be sent to NN when calling get_output (e.g. deterministic = True)

        returns:
            hidden: float(batch_id, memory_id): agent memory at this tick
            policy float[batch_id, action_id]: policy for all actions at this tick
            action: int[batch_id]: picked actions at this tick 
            additional_outputs : any additional outputs provided or an empty list(by default)
            
            
        """
        
        outputs = lasagne.layers.get_output(
            layer_or_layers=[self.memory,self.policy,self.resolver]+additional_outputs,
            inputs= self.input_map(last_memory_state,observation),
            **flags
          )
        hidden,policy,action = outputs[:3]
        
        return hidden,policy,action, outputs[3:]

    def get_sessions(self, 
                     environment,
                     session_length = 10,
                     batch_size = None,
                     initial_env_state = 'zeros',initial_observation = 'zeros',initial_hidden = 'zeros',
                     additional_output_layers = [],
                     **flags
                     ):
        """returns history of agent interaction with environment for given number of turns:
        parameters:
            environment - an environment to interact with (BaseEnvironment instance)
            session_length - how many turns of interaction shall there be for each batch
            batch_size - [required parameter] amount of independed sessions [number or symbolic].Irrelevant if you manually set all initial_*.
            
            initial_<something> - initial values for all variables at 0-th time step
            Unless you are doing something nasty, initial policy (qvalues) and actions will not matter at all
            'zeros' default means filling variable with zeros
            Initial values are NOT included in history sequences
            additional_output_layers - any layers of a network which outputs need to be added to the outputs
            flags: optional flags to be sent to NN when calling get_output (e.g. deterministic = True)


        returns:
            state_seq,observation_seq,hidden_seq,policy_seq,action_seq, [additional_output_0, additional_output_1]
            for environment state, observation, hidden state, agent policy and chosen actions respectively
            each of them having dimensions of [batch_i,seq_i,...]
            
            
            time synchronization policy:
                state_seq,observation_seq correspond to observation BASED ON WHICH agent generated hidden_seq,policy_seq,action_seq
            
        """
        env = environment
        if initial_env_state == 'zeros':
            initial_env_state = T.zeros([batch_size,env.state_size])
        if initial_observation == 'zeros':
            initial_observation = T.zeros([batch_size,env.observation_size])
        if initial_hidden == 'zeros':
            memory_state_shape = lasagne.layers.get_output_shape(self.memory)[1:]
            initial_hidden = T.zeros((batch_size,)+tuple(memory_state_shape))
        
        time_ticks = T.arange(session_length)

        

        #recurrent step function
        #during SCAN, time synchronization is reverse: state_1 came after action_1 based on observation_0 from state_0
        def step(time_tick,env_state,observation,last_hidden,last_policy,last_action,
                 *args):

            hidden,policy,action,additional_outputs = self.get_agent_reaction(last_hidden,observation,
                                                                               additional_output_layers,**flags)
            new_env_state,new_observation = env.get_action_results(env_state,action,time_tick)


            return [new_env_state,new_observation,hidden,policy,action]+additional_outputs

        #main recurrent loop configuration
        additional_init = [None for i in additional_output_layers]
        outputs_info = [initial_env_state,initial_observation,initial_hidden,None,None] + additional_init
        
        history = unroll_scan(step,
            sequences = [time_ticks],
            outputs_info = outputs_info,
            non_sequences = [],
            n_steps = session_length
        )

        self.history = history
        #from [time,batch,...] to [batch,time,...]
        history = [ (var.swapaxes(1,0) if var.ndim >1 else var) for var in history]
        
        #what's inside:
        state_seq,observation_seq,hidden_seq,policy_seq,action_seq = history[:5]
        
        additional_output_sequences = tuple(history[5:])
        
        
        #allign time axes: actions come AFTER states with the same index
        #add first env turn, crop to session length
        state_seq = T.concatenate([insert_dim(initial_env_state,1),
                           state_seq[:,:-1]],axis=1)
        observation_seq = T.concatenate([insert_dim(initial_observation,1),
                           observation_seq[:,:-1]],axis=1)
        
        
        return (state_seq,observation_seq,hidden_seq,policy_seq,action_seq) + additional_output_sequences
                 